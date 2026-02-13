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
st.set_page_config(page_title="AI 부동산 (Final)", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: GOOGLE_API_KEY가 없습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Localhost Only)")
st.caption("내 컴퓨터에서 실행해야만 작동합니다. (네이버 차단 우회 기능 탑재)")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 네이버 크롤링 (좀비 모드: 실패하면 다시 시도)
# --------------------------------------------------------------------------
def get_naver_real_estate_data(region_code, region_name):
    session = requests.Session()
    
    # 전략 1: PC 버전 API (데이터가 가장 정확함)
    url_pc = f"https://new.land.naver.com/api/regions/complexes?cortarNo={region_code}&realEstateType=APT&order=price"
    
    # 전략 2: 모바일 버전 API (보안이 약함, PC 실패 시 시도)
    url_mobile = "https://m.land.naver.com/complex/ajax/complexListByCortarNo"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://new.land.naver.com/",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    
    # [1차 시도] PC 버전으로 접근
    try:
        response = session.get(url_pc, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            complex_list = data.get("complexList", [])
            return parse_data(complex_list, region_name, "PC")
    except:
        pass # 실패하면 조용히 2차 시도로 넘어감

    # [2차 시도] 실패했다면 2초 쉬고 모바일 버전으로 우회 접근
    time.sleep(2)
    try:
        m_params = {"cortarNo": region_code, "rletTpCd": "APT", "order": "price", "tradTpCd": "A1"}
        m_headers = headers.copy()
        m_headers["Referer"] = "https://m.land.naver.com/"
        
        response = session.get(url_mobile, headers=m_headers, params=m_params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            result_list = data.get("result", [])
            return parse_data(result_list, region_name, "Mobile")
    except Exception as e:
        st.toast(f"❌ [{region_name}] 모든 접속 방법 실패: {e}")
        return None
        
    return None

def parse_data(data_list, region_name, source):
    """데이터 파싱 (PC/Mobile 공통 처리)"""
    parsed_data = []
    for item in data_list:
        try:
            # PC와 Mobile의 키(Key) 이름이 다를 수 있어 둘 다 확인
            name = item.get("complexName") or item.get("nm") or ""
            
            # 100세대 미만 제외
            households = item.get("totalHouseholdCount") or item.get("hscpNo") or 0
            # hscpNo는 세대수가 아니지만 모바일엔 세대수 정보가 없어서 일단 통과
            if source == "PC" and households < 100:
                continue

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

tab1, tab2, tab3 = st.tabs(["🏆 추천 랭킹", "🤖 AI 분석", "⚙️ 데이터 수집(Local)"])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("구글 시트 연결 실패")
    st.stop()

# --- TAB 1 & 2: 랭킹 및 분석 (기존 코드 유지 - 생략 없이 작동) ---
with tab1:
    try:
        df_sheet = conn.read(ttl=0)
    except: df_sheet = pd.DataFrame()
    
    if not df_sheet.empty and '매매가(억)' in df_sheet.columns:
        st.subheader("🏆 AI 추천 랭킹")
        df_sheet['매매가(억)'] = pd.to_numeric(df_sheet['매매가(억)'], errors='coerce').fillna(0)
        df_sheet['갭(억)'] = pd.to_numeric(df_sheet['갭(억)'], errors='coerce').fillna(0)
        
        with st.expander("조건 필터", expanded=True):
            price_max = st.slider("최대 매매가", 5, 50, 20)
            
        df_filtered = df_sheet[df_sheet['매매가(억)'] <= price_max]
        st.dataframe(df_filtered.sort_values(by='매매가(억)')[['아파트명','지역','매매가(억)','호가범위']], height=500, use_container_width=True)
    else:
        st.info("데이터가 없습니다. [데이터 수집] 탭으로 이동하세요.")

with tab2:
    st.subheader("💬 AI 자문")
    if not df_sheet.empty and '아파트명' in df_sheet.columns:
        apt = st.selectbox("아파트 선택", df_sheet['아파트명'].unique())
        if st.button("AI 분석"):
            row = df_sheet[df_sheet['아파트명'] == apt].iloc[0]
            prompt = f"매물: {row['아파트명']}, 가격: {row['매매가(억)']}억. 내 자산: {user_cash}억. 매수 조언해줘."
            with st.spinner("분석 중..."):
                res = genai.GenerativeModel('gemini-flash-latest').generate_content(prompt)
                st.write(res.text)

# --- TAB 3: 데이터 수집 (여기가 중요!) ---
with tab3:
    st.header("⚙️ 데이터 수집 (반드시 Localhost에서!)")
    
    # 현재 브라우저 주소가 localhost인지 확인하는 팁
    st.info("📢 주소창이 'localhost:8501'일 때만 작동합니다.")

    naver_regions = {
        "서울 강남구": "1168000000", "서울 서초구": "1165000000", "서울 송파구": "1171000000",
        "경기 성남 분당": "4113500000"
    }
    targets = st.multiselect("수집 지역", list(naver_regions.keys()), default=["서울 강남구"])
    
    if st.button("🚀 수집 시작 (좀비 모드)"):
        bar = st.progress(0, "준비 중...")
        results = []
        for i, reg in enumerate(targets):
            bar.progress((i+1)/len(targets), f"[{reg}] 수집 시도 중...")
            df = get_naver_real_estate_data(naver_regions[reg], reg)
            if df is not None and not df.empty:
                results.append(df)
            time.sleep(2) # 천천히 (차단 방지)
            
        if results:
            final_df = pd.concat(results, ignore_index=True)
            conn.update(data=final_df)
            st.success(f"✅ {len(final_df)}건 저장 완료! 랭킹 탭을 확인하세요.")
        else:
            st.error("❌ 실패: 여전히 차단되었습니다. 잠시 후 다시 시도하거나, 와이파이를 바꿔보세요.")
