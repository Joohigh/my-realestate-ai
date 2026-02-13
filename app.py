import streamlit as st
import pandas as pd
import requests
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
import time
import random

# --------------------------------------------------------------------------
# [1] 설정 및 초기화
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 (Naver Real-time)", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: GOOGLE_API_KEY가 없습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Naver Real-time)")
st.caption("네이버 부동산 실시간 호가 기반 (보안 우회 모드 적용)")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 네이버 부동산 크롤링 (보안 우회)
# --------------------------------------------------------------------------
def get_naver_real_estate_data(region_code, region_name):
    """
    네이버 부동산 모바일 API를 우회 호출하여 호가 데이터를 가져옵니다.
    """
    # [핵심 1] PC 버전 대신 모바일(Mobile) API 엔드포인트 사용
    # cortarNo: 법정동 코드, rletTpCd: APT(아파트), tradTpCd: A1(매매)/B1(전세)
    url = "https://m.land.naver.com/complex/ajax/complexListByCortarNo"
    
    # [핵심 2] 사람인 척 위장하는 강력한 헤더 설정
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://m.land.naver.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    params = {
        "cortarNo": region_code,
        "rletTpCd": "APT",
        "order": "price", # 가격순 정렬
        "tradTpCd": "A1:B1" # 매매+전세
    }
    
    try:
        # 세션을 사용하여 쿠키 유지
        session = requests.Session()
        response = session.get(url, headers=headers, params=params, timeout=10)
        
        # [진단] 상태 코드 확인
        if response.status_code != 200:
            st.warning(f"⚠️ [{region_name}] 접속 차단됨 (Status: {response.status_code})")
            return None
            
        data = response.json()
        complex_list = data.get("result", [])
        
        parsed_data = []
        for item in complex_list:
            try:
                # 데이터 파싱 (모바일 API 구조에 맞춤)
                name = item.get("nm", "") # 단지명
                total_households = item.get("hscpNo", 0) # 세대수 대신 단지번호(hscpNo)가 오지만, 여기선 일단 넘어감
                # 모바일 API는 세대수를 직접 안 주므로, 단지명만 가져오거나 상세 조회 필요
                # 리스트에는 'minPrc'(최저가), 'maxPrc'(최고가)가 들어있음
                
                min_price = item.get("minPrc", 0)
                max_price = item.get("maxPrc", 0)
                
                # 전세가는 같은 리스트에 없어서 매매가 위주로 수집
                # (전세까지 완벽히 하려면 API를 두 번 찔러야 해서 차단 확률 높아짐 -> 매매가만 우선 확보)
                
                # 억 단위 변환 (문자열 "10억 5,000" 형태일 수 있음 -> 숫자만 추출 필요)
                # 하지만 이 API는 숫자로 줌 (단위: 만원)
                
                sale_price_亿 = int(min_price) / 10000 if min_price else 0
                
                if sale_price_亿 > 0:
                    row = {
                        "아파트명": name,
                        "지역": region_name,
                        "매매가(억)": sale_price_亿,
                        "호가범위": f"{int(min_price/10000)}~{int(max_price/10000)}억",
                        "기준일": datetime.now().strftime("%Y-%m-%d")
                    }
                    parsed_data.append(row)
            except:
                continue
                
        return pd.DataFrame(parsed_data)

    except Exception as e:
        st.error(f"❌ [{region_name}] 시스템 에러: {e}")
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
    st.header("🔍 네이버 호가 수집")
    
    # 네이버 법정동 코드 (구 단위)
    naver_regions = {
        "서울 강남구": "1168000000", "서울 서초구": "1165000000", "서울 송파구": "1171000000",
        "서울 용산구": "1117000000", "서울 성동구": "1120000000", "서울 마포구": "1144000000",
        "서울 영등포구": "1156000000", "서울 양천구": "1147000000", "서울 강동구": "1174000000",
        "서울 금천구": "1154500000", "서울 구로구": "1153000000", "서울 관악구": "1162000000",
        "경기 성남 분당": "4113500000", "경기 과천": "4129000000", "경기 하남": "4145000000",
        "경기 안양 동안": "4117300000", "경기 수원 영통": "4111700000", "경기 광명": "4121000000"
    }
    
    selected_regions = st.multiselect("수집할 지역 선택", list(naver_regions.keys()), default=["서울 금천구"])
    
    if st.button("🚀 네이버 호가 가져오기"):
        progress_bar = st.progress(0, text="네이버 서버에 접속 시도 중...")
        all_data = []
        
        for i, region_name in enumerate(selected_regions):
            code = naver_regions[region_name]
            progress_bar.progress((i + 1) / len(selected_regions), text=f"[{region_name}] 데이터 수신 중...")
            
            df_region = get_naver_real_estate_data(code, region_name)
            if df_region is not None and not df_region.empty:
                all_data.append(df_region)
            else:
                # 데이터가 비었다면 구 단위가 막힌 것일 수 있음 -> 동 단위로 우회 필요 (복잡도 증가)
                pass
            
            # [중요] 네이버 차단 방지를 위해 랜덤하게 쉬기
            time.sleep(random.uniform(1.0, 2.0))
            
        progress_bar.empty()
        
        if all_data:
            final_df = pd.concat(all_data, ignore_index=True)
            # 전세가/갭 정보가 없으므로 추정치 사용 (API 제한)
            final_df['전세가(억)'] = final_df['매매가(억)'] * 0.6
            final_df['갭(억)'] = final_df['매매가(억)'] - final_df['전세가(억)']
            
            st.session_state['naver_data'] = final_df
            st.success(f"✅ 총 {len(final_df)}개 단지의 실시간 호가 수집 성공!")
        else:
            st.error("❌ 데이터를 가져오지 못했습니다.")
            st.info("💡 **팁:** Streamlit Cloud 서버 IP가 네이버에 의해 차단되었을 가능성이 매우 높습니다. 이 경우 이 코드는 **사용자님의 PC(로컬 환경)**에서 실행해야만 작동합니다.")

# --------------------------------------------------------------------------
# [3] 메인 화면
# --------------------------------------------------------------------------
tab1, tab2 = st.tabs(["📋 호가 랭킹 & 필터", "🤖 AI 호가 분석 & 채팅"])

from datetime import datetime

# --- TAB 1: 랭킹 및 필터 ---
with tab1:
    if 'naver_data' in st.session_state:
        df = st.session_state['naver_data']
        
        with st.expander("🕵️‍♂️ 호가 기준 필터링 (펼치기)", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                st.write("💰 **매매 호가 (억)**")
                price_range = st.slider("매매가 범위", 0, 50, (5, 30))
            with c2:
                st.write("💸 **예상 갭 (억)**")
                gap_range = st.slider("최대 예상 갭", 1, 20, 10)
        
        mask = (
            (df['매매가(억)'] >= price_range[0]) & 
            (df['매매가(억)'] <= price_range[1]) &
            (df['갭(억)'] <= gap_range)
        )
        df_filtered = df[mask].sort_values(by='매매가(억)')
        
        st.subheader(f"🔥 매매 추천 (저렴한 순) - {len(df_filtered)}건")
        st.dataframe(
            df_filtered[['아파트명', '지역', '매매가(억)', '호가범위', '갭(억)']].style.format({'매매가(억)': '{:.1f}', '갭(억)': '{:.1f}'}),
            height=600, use_container_width=True
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
        
        if 'last_apt' not in st.session_state: st.session_state['last_apt'] = None
        if selected_apt != st.session_state['last_apt']:
            st.session_state['messages'] = []
            st.session_state['last_apt'] = selected_apt
        
        if selected_apt:
            row = df[df['아파트명'] == selected_apt].iloc[0]
            
            c1, c2, c3 = st.columns(3)
            c1.metric("최저 호가", f"{row['매매가(억)']}억")
            c2.metric("예상 전세가", f"{row['전세가(억)']}억")
            c3.metric("예상 갭", f"{row['갭(억)']}억")
            
            if st.button("🚀 이 호가로 살만할까? (AI 분석)"):
                prompt = f"""
                당신은 부동산 전문가입니다. 현재 네이버 부동산 '호가' 기준으로 조언해주세요.
                [매물] {row['아파트명']} ({row['지역']}), 최저호가 {row['매매가(억)']}억, 호가범위 {row['호가범위']}
                [재정] 현금 {user_cash}억, 연소득 {user_income}천만
                이 가격이 적정한지, 내 자금으로 매수 가능한지, 향후 전망은 어떤지 분석해줘.
                """
                
                with st.spinner("분석 중..."):
                    try:
                        model = genai.GenerativeModel('gemini-flash-latest')
                        response = model.generate_content(prompt)
                        st.session_state['messages'].append({"role": "assistant", "content": response.text})
                    except Exception as e: st.error(f"AI 분석 실패: {e}")
            
            for msg in st.session_state.get('messages', []):
                with st.chat_message(msg['role']): st.markdown(msg['content'])
            
            if user_input := st.chat_input("추가 질문 입력"):
                with st.chat_message("user"): st.markdown(user_input)
                st.session_state['messages'].append({"role": "user", "content": user_input})
                
                with st.chat_message("assistant"):
                    with st.spinner("답변 중..."):
                        try:
                            model = genai.GenerativeModel('gemini-flash-latest')
                            history = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state['messages'][-4:]])
                            final_prompt = f"{history}\nUser: {user_input}\nAssistant:"
                            response = model.generate_content(final_prompt)
                            st.markdown(response.text)
                            st.session_state['messages'].append({"role": "assistant", "content": response.text})
                        except Exception as e: st.error(f"오류: {e}")
    else:
        st.info("데이터를 먼저 수집해주세요.")
