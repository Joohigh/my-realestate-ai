import streamlit as st
import pandas as pd
import requests
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
import time
import random
from datetime import datetime

# --------------------------------------------------------------------------
# [1] 기본 설정
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 (Full Version)", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: GOOGLE_API_KEY가 없습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Seoul & Gyeonggi)")
st.caption("서울 전역 + 경기 핵심지 네이버 호가 분석 (Localhost 실행 전용)")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 네이버 크롤링 (차단 회피 + 재시도 로직)
# --------------------------------------------------------------------------
def get_naver_real_estate_data(region_code, region_name):
    session = requests.Session()
    
    # 전략: PC API 우선 시도 -> 실패 시 모바일 API 시도
    url_pc = f"https://new.land.naver.com/api/regions/complexes?cortarNo={region_code}&realEstateType=APT&order=price"
    url_mobile = "https://m.land.naver.com/complex/ajax/complexListByCortarNo"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://new.land.naver.com/",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    
    # [1차] PC 버전 시도
    try:
        response = session.get(url_pc, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return parse_data(data.get("complexList", []), region_name, "PC")
    except: pass

    # [2차] 모바일 버전 시도 (잠시 대기 후)
    time.sleep(1.5)
    try:
        m_params = {"cortarNo": region_code, "rletTpCd": "APT", "order": "price", "tradTpCd": "A1"}
        m_headers = headers.copy()
        m_headers["Referer"] = "https://m.land.naver.com/"
        response = session.get(url_mobile, headers=m_headers, params=m_params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return parse_data(data.get("result", []), region_name, "Mobile")
    except: pass
    
    st.toast(f"❌ [{region_name}] 수집 실패 (네이버 차단)")
    return None

def parse_data(data_list, region_name, source):
    parsed_data = []
    for item in data_list:
        try:
            name = item.get("complexName") or item.get("nm") or ""
            # 100세대 미만 제외 (노이즈 제거)
            households = item.get("totalHouseholdCount") or item.get("hscpNo") or 0
            if source == "PC" and households < 100: continue

            min_price = item.get("minDealPrice") or item.get("minPrc") or 0
            max_price = item.get("maxDealPrice") or item.get("maxPrc") or 0
            
            sale_price_val = int(min_price) / 10000 if min_price else 0
            
            if sale_price_val > 0:
                row = {
                    "아파트명": name,
                    "지역": region_name,
                    "매매가(억)": sale_price_val,
                    "전세가(억)": sale_price_val * 0.6,
                    "갭(억)": sale_price_val * 0.4,
                    "호가범위": f"{int(min_price/10000)}~{int(max_price/10000)}억",
                    "수집일": datetime.now().strftime("%Y-%m-%d")
                }
                parsed_data.append(row)
        except: continue
    return pd.DataFrame(parsed_data)

# --------------------------------------------------------------------------
# [2] 사이드바 & 메인
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 자산 설정")
    user_cash = st.number_input("가용 현금 (억 원)", 0.0, 100.0, 3.0, 0.1)
    user_income = st.number_input("연 소득 (천만 원)", 0.0, 100.0, 8.0, 0.5)
    st.divider()
    st.info("💡 데이터 수집은 [데이터 수집] 탭에서 진행하세요.")

tab1, tab2, tab3 = st.tabs(["🏆 추천 랭킹", "🤖 AI 분석", "⚙️ 데이터 수집(Local)"])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("구글 시트 연결 실패")
    st.stop()

# --- TAB 1: 랭킹 ---
with tab1:
    try: df_sheet = conn.read(ttl=0)
    except: df_sheet = pd.DataFrame()
    
    if not df_sheet.empty and '매매가(억)' in df_sheet.columns:
        # 데이터 전처리
        df_sheet['매매가(억)'] = pd.to_numeric(df_sheet['매매가(억)'], errors='coerce').fillna(0)
        df_sheet['갭(억)'] = pd.to_numeric(df_sheet['갭(억)'], errors='coerce').fillna(0)
        
        # 필터
        with st.expander("🕵️‍♂️ 조건 검색", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1: price_max = st.slider("최대 매매가", 5, 50, 20)
            with c2: gap_max = st.slider("최대 갭 투자금", 1, 20, 10)
            with c3: 
                regions = ["전체"] + sorted(df_sheet['지역'].unique().tolist())
                sel_region = st.selectbox("지역 필터", regions)
        
        df_filtered = df_sheet[(df_sheet['매매가(억)'] <= price_max) & (df_sheet['갭(억)'] <= gap_max)]
        if sel_region != "전체":
            df_filtered = df_filtered[df_filtered['지역'] == sel_region]
            
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🏡 매매 추천 (저렴한 순)")
            st.dataframe(df_filtered.sort_values(by='매매가(억)')[['아파트명','지역','매매가(억)','호가범위']], height=500, use_container_width=True)
        with c2:
            st.subheader("💰 갭투자 추천 (갭 순)")
            st.dataframe(df_filtered.sort_values(by='갭(억)')[['아파트명','지역','매매가(억)','갭(억)']], height=500, use_container_width=True)
    else:
        st.info("데이터가 없습니다. [데이터 수집] 탭을 이용하세요.")

# --- TAB 2: AI 분석 ---
with tab2:
    if not df_sheet.empty and '아파트명' in df_sheet.columns:
        st.subheader("💬 AI 부동산 자문")
        apt = st.selectbox("아파트 선택", sorted(df_sheet['아파트명'].unique()))
        
        if st.button("🚀 AI 분석 시작"):
            row = df_sheet[df_sheet['아파트명'] == apt].iloc[0]
            prompt = f"""
            [매물] {row['아파트명']} ({row['지역']}), 호가 {row['매매가(억)']}억.
            [자산] 현금 {user_cash}억, 소득 {user_income}천만.
            이 매물의 적정성과 매수 가능 여부를 분석해줘.
            """
            with st.spinner("분석 중..."):
                res = genai.GenerativeModel('gemini-flash-latest').generate_content(prompt)
                st.markdown(res.text)

# --- TAB 3: 데이터 수집 (전체 지역 포함) ---
with tab3:
    st.header("⚙️ 데이터 수집 및 업데이트")
    st.info("※ 한 번에 너무 많은 지역을 선택하면 네이버가 차단할 수 있습니다. 3~5개씩 나누어 수집하는 것을 추천합니다.")

    # [약속된 전체 지역 목록]
    naver_regions = {
        # 서울 25개 구
        "서울 강남구": "1168000000", "서울 강동구": "1174000000", "서울 강북구": "1130500000", 
        "서울 강서구": "1150000000", "서울 관악구": "1162000000", "서울 광진구": "1121500000", 
        "서울 구로구": "1153000000", "서울 금천구": "1154500000", "서울 노원구": "1135000000", 
        "서울 도봉구": "1132000000", "서울 동대문구": "1123000000", "서울 동작구": "1159000000", 
        "서울 마포구": "1144000000", "서울 서대문구": "1141000000", "서울 서초구": "1165000000", 
        "서울 성동구": "1120000000", "서울 성북구": "1129000000", "서울 송파구": "1171000000", 
        "서울 양천구": "1147000000", "서울 영등포구": "1156000000", "서울 용산구": "1117000000", 
        "서울 은평구": "1138000000", "서울 종로구": "1111000000", "서울 중구": "1114000000", 
        "서울 중랑구": "1126000000",
        
        # 경기 핵심 투자처
        "경기 성남 분당": "4113500000", "경기 성남 수정(판교/위례)": "4113100000",
        "경기 과천": "4129000000", "경기 광명": "4121000000", 
        "경기 안양 동안(평촌)": "4117300000", "경기 수원 영통(광교)": "4111700000", 
        "경기 용인 수지": "4146500000", "경기 하남(미사)": "4145000000", 
        "경기 화성(동탄)": "4159000000"
    }
    
    # 멀티 셀렉트 박스 (기본값 없음)
    targets = st.multiselect("수집할 지역을 선택하세요 (전체 선택 가능)", list(naver_regions.keys()))
    
    # '전체 선택' 편의 버튼
    if st.checkbox("모든 지역 선택하기 (주의: 시간 오래 걸림)"):
        targets = list(naver_regions.keys())

    if st.button("🚀 선택한 지역 수집 시작"):
        if not targets:
            st.error("지역을 하나 이상 선택해주세요.")
        else:
            bar = st.progress(0, "수집 준비...")
            results = []
            
            for i, reg in enumerate(targets):
                bar.progress((i+1)/len(targets), f"[{reg}] 수집 중... ({i+1}/{len(targets)})")
                df = get_naver_real_estate_data(naver_regions[reg], reg)
                if df is not None and not df.empty:
                    results.append(df)
                
                # 차단 방지를 위한 랜덤 대기 (필수)
                time.sleep(random.uniform(2.0, 4.0))
            
            bar.empty()
            
            if results:
                final_df = pd.concat(results, ignore_index=True)
                conn.update(data=final_df)
                st.success(f"✅ 총 {len(final_df)}개 단지 저장 완료! [추천 랭킹] 탭을 확인하세요.")
            else:
                st.error("❌ 수집 실패 (네이버 차단됨). 잠시 후 다시 시도하세요.")
