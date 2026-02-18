import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai

# --------------------------------------------------------------------------
# [1] 설정
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 분석", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("secrets.toml 설정이 필요합니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 투자 솔루션")
st.caption("구글 시트(RealEstate_DB) 기반 실시간 분석 시스템")
st.markdown("---")

# --------------------------------------------------------------------------
# [2] 사이드바: 자산 설정
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 자산 설정")
    user_cash = st.number_input("가용 현금 (억 원)", 0.0, 100.0, 3.0, 0.1)
    user_income = st.number_input("연 소득 (천만 원)", 0.0, 100.0, 8.0, 0.5)
    st.divider()
    st.info("ℹ️ 데이터 업데이트는 로컬의 'collector.py'를 이용하세요.")

# --------------------------------------------------------------------------
# [3] 데이터 로드 (DB 읽기 전용)
# --------------------------------------------------------------------------
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(ttl=0) # 항상 최신 데이터 로드
except:
    st.error("구글 시트 연결 실패")
    st.stop()

# 데이터 전처리 (에러 방지)
if not df.empty:
    cols = ['매매호가(억)', '예상갭(억)', '평형']
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    
    # 탭 구성
    tab1, tab2 = st.tabs(["🏆 호가 랭킹", "🤖 AI 심층 상담"])

    # ======================================================================
    # TAB 1: 랭킹
    # ======================================================================
    with tab1:
        st.header("🏆 맞춤형 추천 랭킹")
        
        with st.expander("조건 검색 (필터)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1: pyung_range = st.slider("평형", 10, 60, (20, 35))
            with c2: price_max = st.slider("최대 가격(억)", 3, 50, 15)
            with c3: 
                regions = ["전체"] + sorted(df['지역'].unique().tolist())
                sel_region = st.selectbox("지역", regions)
        
        filtered = df[
            (df['평형'] >= pyung_range[0]) & (df['평형'] <= pyung_range[1]) &
            (df['매매호가(억)'] <= price_max) & (df['매매호가(억)'] > 0)
        ]
        if sel_region != "전체": filtered = filtered[filtered['지역'] == sel_region]
        
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🏡 실거주 추천 (저가순)")
            st.dataframe(filtered.sort_values('매매호가(억)')[['아파트명','평형','지역','매매호가(억)']], height=500, use_container_width=True)
        with c2:
            st.subheader("💰 갭투자 추천 (갭순)")
            st.dataframe(filtered.sort_values('예상갭(억)')[['아파트명','평형','지역','매매호가(억)','예상갭(억)']], height=500, use_container_width=True)

    # ======================================================================
    # TAB 2: AI 상담
    # ======================================================================
    with tab2:
        st.header("💬 AI 부동산 자문")
        
        apts = sorted(df['아파트명'].unique())
        sel_apt = st.selectbox("아파트 선택", apts, index=None)
        
        # 세션 관리
        if 'chat' not in st.session_state or sel_apt != st.session_state.get('last'):
            st.session_state['chat'] = []
            st.session_state['last'] = sel_apt
            
        if sel_apt:
            row = df[df['아파트명'] == sel_apt].iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("평형", f"{row['평형']}평")
            c2.metric("매매호가", f"{row['매매호가(억)']}억")
            c3.metric("예상갭", f"{row['예상갭(억)']}억")
            
            if st.button("🚀 AI 분석 시작", type="primary"):
                prompt = f"""
                [매물] {row['아파트명']} ({row['지역']}), {row['평형']}평, 호가 {row['매매호가(억)']}억.
                [자산] 현금 {user_cash}억, 소득 {user_income}천만.
                이 호가의 적정성과 매수 가능 여부를 분석해줘.
                """
                with st.spinner("분석 중..."):
                    try:
                        res = genai.GenerativeModel('gemini-flash-latest').generate_content(prompt)
                        st.session_state['chat'].append({"role": "assistant", "content": res.text})
                    except: st.error("AI 오류")
            
            for msg in st.session_state['chat']:
                with st.chat_message(msg['role']): st.markdown(msg['content'])
            
            if txt := st.chat_input("질문 입력"):
                st.session_state['chat'].append({"role": "user", "content": txt})
                with st.chat_message("user"): st.markdown(txt)
                with st.chat_message("assistant"):
                    with st.spinner("..."):
                        hist = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state['chat'][-3:]])
                        res = genai.GenerativeModel('gemini-flash-latest').generate_content(f"{hist}\nUser: {txt}")
                        st.markdown(res.text)
                        st.session_state['chat'].append({"role": "assistant", "content": res.text})

else:
    st.warning("⚠️ 저장된 데이터가 없습니다. 로컬에서 수집기를 실행해주세요.")
