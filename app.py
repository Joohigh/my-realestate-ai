import streamlit as st
import pandas as pd
import requests
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
import time
import json

# --------------------------------------------------------------------------
# [1] 설정 및 초기화
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 (네이버 호가)", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: GOOGLE_API_KEY가 없습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Naver Real-time)")
st.caption("네이버 부동산 실시간 호가 기반 분석 (실거래가 아님, 현재 시장가격)")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 네이버 부동산 크롤링 (핵심 로직)
# --------------------------------------------------------------------------
def get_naver_real_estate_data(region_code, region_name):
    """
    네이버 부동산 내부 API를 호출하여 해당 지역(구)의 아파트 단지 목록과 시세(호가)를 가져옵니다.
    """
    # 네이버 부동산 지역별 단지 목록 조회 API
    url = f"https://new.land.naver.com/api/regions/complexes?cortarNo={region_code}&realEstateType=APT&order="
    
    # 봇 탐지 방지용 헤더
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://new.land.naver.com/"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            complex_list = data.get("complexList", [])
            
            parsed_data = []
            for item in complex_list:
                # 네이버 데이터 파싱
                # price: 매매 최저가 (단위: 만원)
                # leasePrice: 전세 최저가 (단위: 만원)
                try:
                    name = item.get("complexName", "")
                    total_households = item.get("totalHouseholdCount", 0)
                    
                    # 100세대 미만 소형 단지는 노이즈 제거를 위해 제외 (선택사항)
                    if total_households < 100:
                        continue
                        
                    min_sale_price = item.get("minDealPrice", 0) # 매매 최저 호가
                    max_sale_price = item.get("maxDealPrice", 0) # 매매 최고 호가
                    min_lease_price = item.get("minLeasePrice", 0) # 전세 최저 호가
                    
                    # 평형 정보는 목록 API에서 제공하지 않으므로, 대표 평형이나 전체 범위를 뭉뚱그려 처리
                    # (상세 크롤링은 너무 느려지므로, 여기서는 단지별 '최저가격' 기준으로 분석)
                    
                    # 억 단위 변환
                    sale_price_亿 = int(min_sale_price) / 10000 if min_sale_price else 0
                    lease_price_亿 = int(min_lease_price) / 10000 if min_lease_price else 0
                    
                    if sale_price_亿 > 0: # 매매가 있는 것만
                        row = {
                            "아파트명": name,
                            "지역": region_name,
                            "세대수": total_households,
                            "매매가(억)": sale_price_亿, # 최저 호가 기준
                            "전세가(억)": lease_price_亿, # 최저 호가 기준
                            "전세가율(%)": round((lease_price_亿 / sale_price_亿 * 100), 1) if sale_price_亿 > 0 else 0,
                            "호가범위": f"{int(min_sale_price/10000)}~{int(max_sale_price/10000)}억",
                            # 네이버는 실시간 호가이므로 거래일이 없음 -> 수집일로 대체
                            "기준일": datetime.now().strftime("%Y-%m-%d")
                        }
                        parsed_data.append(row)
                except:
                    continue
                    
            return pd.DataFrame(parsed_data)
        else:
            return None
    except Exception as e:
        return None

# --------------------------------------------------------------------------
# [2] 사이드바 (설정)
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 재정 상황")
    with st.expander("💸 자산 및 소득 입력", expanded=True):
        user_cash = st.number_input("가용 현금 (억 원)", 0.0, 100.0, 3.0, 0.1)
        user_income = st.number_input("연 소득 (천만 원)", 0.0, 50.0, 8.0, 0.5)
        target_loan_rate = st.slider("대출 금리 (%)", 2.0, 8.0, 4.0)
    
    st.divider()
    st.header("🔍 네이버 부동산 호가 수집")
    
    # 네이버 법정동 코드 (CortarNo) 매핑 - 주요 지역
    # (네이버는 행정동 코드가 아닌 법정동 코드를 씁니다)
    naver_regions = {
        "서울 강남구": "1168000000", "서울 서초구": "1165000000", "서울 송파구": "1171000000",
        "서울 용산구": "1117000000", "서울 성동구": "1120000000", "서울 마포구": "1144000000",
        "서울 영등포구": "1156000000", "서울 양천구": "1147000000", "서울 강동구": "1174000000",
        "서울 종로구": "1111000000", "서울 중구": "1114000000", "서울 노원구": "1135000000",
        "경기 성남 분당": "4113500000", "경기 과천": "4129000000", "경기 하남": "4145000000",
        "경기 안양 동안(평촌)": "4117300000", "경기 수원 영통(광교)": "4111700000",
        "경기 화성(동탄)": "4159000000", "경기 용인 수지": "4146500000", "경기 광명": "4121000000"
    }
    
    selected_regions = st.multiselect("수집할 지역 선택 (여러 개 가능)", list(naver_regions.keys()), default=["서울 강남구"])
    
    if st.button("🚀 네이버 호가 가져오기"):
        progress_bar = st.progress(0, text="네이버 부동산 접속 중...")
        all_data = []
        
        for i, region_name in enumerate(selected_regions):
            code = naver_regions[region_name]
            progress_bar.progress((i + 1) / len(selected_regions), text=f"[{region_name}] 매물 정보 긁어오는 중...")
            
            df_region = get_naver_real_estate_data(code, region_name)
            if df_region is not None and not df_region.empty:
                all_data.append(df_region)
            
            time.sleep(0.5) # 네이버 차단 방지 딜레이
            
        progress_bar.empty()
        
        if all_data:
            final_df = pd.concat(all_data, ignore_index=True)
            # 갭 계산
            final_df['갭(억)'] = final_df['매매가(억)'] - final_df['전세가(억)']
            # 데이터 저장
            st.session_state['naver_data'] = final_df
            st.success(f"✅ 총 {len(final_df)}개 단지의 최신 호가 수집 완료!")
        else:
            st.error("데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")

# --------------------------------------------------------------------------
# [3] 메인 화면
# --------------------------------------------------------------------------
tab1, tab2 = st.tabs(["📋 호가 랭킹 & 필터", "🤖 AI 호가 분석 & 채팅"])

from datetime import datetime

# --- TAB 1: 랭킹 및 필터 ---
with tab1:
    if 'naver_data' in st.session_state:
        df = st.session_state['naver_data']
        
        # 필터 UI
        with st.expander("🕵️‍♂️ 호가 기준 필터링 (펼치기)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.write("💰 **매매 호가 (억)**")
                price_range = st.slider("매매가 범위", 0, 50, (10, 30))
            with c2:
                st.write("💸 **투자금 (갭)**")
                gap_range = st.slider("갭(매매-전세) 범위", 0, 20, (1, 10))
            with c3:
                st.write("🏢 **세대수**")
                min_house = st.slider("최소 세대수", 100, 3000, 500)
        
        # 필터 적용
        mask = (
            (df['매매가(억)'] >= price_range[0]) & 
            (df['매매가(억)'] <= price_range[1]) &
            (df['갭(억)'] <= gap_range[1]) &
            (df['세대수'] >= min_house)
        )
        df_filtered = df[mask].sort_values(by='매매가(억)')
        
        # 결과 표시
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"🔥 매매 추천 (저렴한 순) - {len(df_filtered)}건")
            st.dataframe(
                df_filtered[['아파트명', '지역', '매매가(억)', '호가범위', '세대수']].style.format({'매매가(억)': '{:.1f}'}),
                height=500, use_container_width=True
            )
        
        with col2:
            st.subheader(f"💰 갭투자 추천 (갭 작은 순)")
            # 전세가 0인(전세매물 없는) 경우 제외
            df_gap = df_filtered[df_filtered['전세가(억)'] > 0].sort_values(by='갭(억)')
            st.dataframe(
                df_gap[['아파트명', '지역', '매매가(억)', '전세가(억)', '갭(억)']].style.format({'매매가(억)': '{:.1f}', '전세가(억)': '{:.1f}', '갭(억)': '{:.1f}'}),
                height=500, use_container_width=True
            )
            
    else:
        st.info("👈 왼쪽 사이드바에서 [네이버 호가 가져오기] 버튼을 눌러주세요.")

# --- TAB 2: AI 분석 ---
with tab2:
    st.header("🤖 네이버 부동산 AI 분석관")
    st.caption("현재 시장에 나와있는 '호가'를 기준으로 분석합니다.")
    
    if 'naver_data' in st.session_state:
        df = st.session_state['naver_data']
        apt_list = sorted(df['아파트명'].unique())
        selected_apt = st.selectbox("분석할 단지 선택", apt_list)
        
        # 채팅 기록 초기화 로직
        if 'last_apt' not in st.session_state: st.session_state['last_apt'] = None
        if selected_apt != st.session_state['last_apt']:
            st.session_state['messages'] = []
            st.session_state['last_apt'] = selected_apt
        
        if selected_apt:
            row = df[df['아파트명'] == selected_apt].iloc[0]
            
            c1, c2, c3 = st.columns(3)
            c1.metric("최저 호가", f"{row['매매가(억)']}억")
            c2.metric("전세 호가", f"{row['전세가(억)']}억")
            c3.metric("갭 투자금", f"{row['갭(억)']}억")
            
            # AI 분석 버튼
            if st.button("🚀 이 호가로 살만할까? (AI 분석)"):
                prompt = f"""
                당신은 부동산 전문가입니다. 현재 네이버 부동산에 올라온 '호가'를 기준으로 매수 조언을 해주세요.
                
                [매물 정보]
                - 아파트: {row['아파트명']} ({row['지역']})
                - 현재 최저 호가(Asking Price): {row['매매가(억)']}억
                - 현재 전세 호가: {row['전세가(억)']}억
                - 갭(차이): {row['갭(억)']}억
                - 세대수: {row['세대수']}세대
                
                [사용자 재정]
                - 가용현금: {user_cash}억
                - 연소득: {user_income}천만
                
                1. 이 호가가 적정한 수준인지(일반적인 평가), 
                2. 사용자의 자금으로 매수가 가능한지(영끌 여부),
                3. 향후 전망은 어떤지 분석해서 마크다운으로 답변해줘.
                """
                
                with st.spinner("네이버 호가를 분석 중입니다..."):
                    try:
                        model = genai.GenerativeModel('gemini-flash-latest')
                        response = model.generate_content(prompt)
                        st.session_state['messages'].append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        st.error(f"AI 분석 실패: {e}")
            
            # 채팅 UI
            for msg in st.session_state.get('messages', []):
                with st.chat_message(msg['role']):
                    st.markdown(msg['content'])
            
            if user_input := st.chat_input("추가 질문 (예: 지금 호가 좀 비싼거 아냐?)"):
                with st.chat_message("user"):
                    st.markdown(user_input)
                st.session_state['messages'].append({"role": "user", "content": user_input})
                
                with st.chat_message("assistant"):
                    with st.spinner("답변 생성 중..."):
                        try:
                            model = genai.GenerativeModel('gemini-flash-latest')
                            # 문맥 유지
                            history_text = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state['messages'][-4:]])
                            final_prompt = f"{history_text}\nUser: {user_input}\nAssistant:"
                            
                            response = model.generate_content(final_prompt)
                            st.markdown(response.text)
                            st.session_state['messages'].append({"role": "assistant", "content": response.text})
                        except Exception as e:
                            st.error(f"오류: {e}")

    else:
        st.info("데이터를 먼저 수집해주세요.")
