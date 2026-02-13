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
st.set_page_config(page_title="AI 부동산 (Naver Mobile)", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: GOOGLE_API_KEY가 없습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Mobile Bypass)")
st.caption("네이버 모바일 API 우회 접속 (차단 회피 최적화)")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 네이버 모바일 크롤링 (차단 회피 핵심)
# --------------------------------------------------------------------------
def get_naver_real_estate_data(region_code, region_name):
    # [핵심] 모바일 전용 주소 사용 (보안이 덜 까다로움)
    url = "https://m.land.naver.com/complex/ajax/complexListByCortarNo"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
        "Referer": "https://m.land.naver.com/",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    # rletTpCd: APT(아파트), tradTpCd: A1(매매)
    params = {
        "cortarNo": region_code,
        "rletTpCd": "APT",
        "order": "price",
        "tradTpCd": "A1"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        # 접속 실패 시
        if response.status_code != 200:
            st.error(f"⚠️ [{region_name}] 서버 응답 오류: {response.status_code}")
            return None
            
        data = response.json()
        result_list = data.get("result", [])
        
        parsed_data = []
        for item in result_list:
            try:
                # [중요] 모바일 데이터는 이름표가 다릅니다 (nm, minPrc 등)
                name = item.get("nm", "") # 단지명
                
                # 가격 정보 (단위: 만원)
                min_price = item.get("minPrc", 0) 
                max_price = item.get("maxPrc", 0)
                
                # 억 단위 변환
                sale_price_val = int(min_price) / 10000 if min_price else 0
                
                if sale_price_val > 0:
                    row = {
                        "아파트명": name,
                        "지역": region_name,
                        "매매가(억)": sale_price_val,
                        # 모바일 리스트에는 전세가가 같이 안 와서, 매매가의 60%로 추정 (안전하게)
                        "전세가(억)": sale_price_val * 0.6, 
                        "갭(억)": sale_price_val * 0.4, 
                        "호가범위": f"{int(min_price/10000)}~{int(max_price/10000)}억",
                        "수집일": datetime.now().strftime("%Y-%m-%d")
                    }
                    parsed_data.append(row)
            except: continue
            
        return pd.DataFrame(parsed_data)

    except Exception as e:
        st.error(f"❌ [{region_name}] 시스템 에러: {e}")
        return None

# --------------------------------------------------------------------------
# [2] 사이드바: 자산 설정
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 자산 설정")
    user_cash = st.number_input("가용 현금 (억 원)", 0.0, 100.0, 3.0, 0.1)
    user_income = st.number_input("연 소득 (천만 원)", 0.0, 100.0, 8.0, 0.5)
    
    st.divider()
    st.info("💡 데이터 업데이트가 필요할 때만 [데이터 관리] 탭을 이용하세요.")

# --------------------------------------------------------------------------
# [3] 메인 기능
# --------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["🏆 추천 랭킹", "🤖 AI 심층 분석 & 채팅", "⚙️ 데이터 관리(수집)"])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("구글 시트 연결 실패.")
    st.stop()

# ==========================================================================
# TAB 1: 추천 랭킹
# ==========================================================================
with tab1:
    st.header("🏆 AI 추천 랭킹")
    try:
        df_sheet = conn.read(ttl=0)
    except:
        df_sheet = pd.DataFrame()
    
    # 데이터 유효성 검사
    required_cols = ['아파트명', '지역', '매매가(억)', '갭(억)', '호가범위']
    is_valid_data = not df_sheet.empty and all(col in df_sheet.columns for col in required_cols)

    if is_valid_data:
        # 필터 UI
        with st.expander("🕵️‍♂️ 조건 검색 (필터)", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                price_max = st.slider("최대 매매가 (억)", 5, 50, 20)
            with c2:
                gap_max = st.slider("최대 투자금 (갭)", 1, 20, 10)
            with c3:
                all_regions = ["전체"] + sorted(df_sheet['지역'].unique().tolist())
                selected_region = st.selectbox("지역 선택", all_regions)

        # 형변환
        df_sheet['매매가(억)'] = pd.to_numeric(df_sheet['매매가(억)'], errors='coerce').fillna(0)
        df_sheet['갭(억)'] = pd.to_numeric(df_sheet['갭(억)'], errors='coerce').fillna(0)
        
        df_filtered = df_sheet[
            (df_sheet['매매가(억)'] <= price_max) & 
            (df_sheet['갭(억)'] <= gap_max)
        ]
        
        if selected_region != "전체":
            df_filtered = df_filtered[df_filtered['지역'] == selected_region]
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f"🏡 실거주 추천")
            if not df_filtered.empty:
                st.dataframe(
                    df_filtered.sort_values(by='매매가(억)')[['아파트명', '지역', '매매가(억)', '호가범위']].style.format({'매매가(억)': '{:.1f}'}),
                    height=500, use_container_width=True
                )
            else: st.info("매물이 없습니다.")
            
        with col2:
            st.subheader(f"💰 갭투자 추천")
            if not df_filtered.empty:
                st.dataframe(
                    df_filtered.sort_values(by='갭(억)')[['아파트명', '지역', '매매가(억)', '갭(억)']].style.format({'매매가(억)': '{:.1f}', '갭(억)': '{:.1f}'}),
                    height=500, use_container_width=True
                )
            else: st.info("매물이 없습니다.")
    else:
        st.warning("⚠️ 데이터가 없습니다. [데이터 관리] 탭에서 수집해주세요.")

# ==========================================================================
# TAB 2: AI 심층 분석
# ==========================================================================
with tab2:
    st.header("💬 AI 부동산 투자 자문")
    
    if is_valid_data:
        all_apts = sorted(df_sheet['아파트명'].unique())
        selected_apt = st.selectbox("상담할 아파트 선택", all_apts, index=None, placeholder="아파트를 선택하세요...")
        
        if 'chat_history' not in st.session_state: st.session_state['chat_history'] = []
        if 'last_apt' not in st.session_state: st.session_state['last_apt'] = None
        
        if selected_apt != st.session_state['last_apt']:
            st.session_state['chat_history'] = []
            st.session_state['last_apt'] = selected_apt
            
        if selected_apt:
            target_row = df_sheet[df_sheet['아파트명'] == selected_apt].iloc[0]
            
            c1, c2, c3 = st.columns(3)
            c1.metric("현재 호가", f"{target_row['매매가(억)']}억")
            c2.metric("예상 전세", f"{target_row['전세가(억)']}억")
            c3.metric("필요 갭", f"{target_row['갭(억)']}억")
            
            if st.button("🚀 AI 심층 분석 시작", type="primary"):
                prompt = f"""
                당신은 부동산 투자 전문가입니다. 
                [매물] {target_row['아파트명']} ({target_row['지역']})
                - 현재호가: {target_row['매매가(억)']}억 (호가범위: {target_row['호가범위']})
                - 사용자 자금: 현금 {user_cash}억, 연소득 {user_income}천만
                
                1. 가격 적정성 평가
                2. 매수 가능 여부 (영끌 위험도)
                3. 향후 전망 및 투자 가치
                
                위 내용을 마크다운으로 정리해줘.
                """
                with st.spinner("분석 중..."):
                    try:
                        model = genai.GenerativeModel('gemini-flash-latest')
                        res = model.generate_content(prompt)
                        st.session_state['chat_history'].append({"role": "assistant", "content": res.text})
                    except Exception as e: st.error(f"오류: {e}")
            
            for msg in st.session_state['chat_history']:
                with st.chat_message(msg['role']): st.markdown(msg['content'])
            
            if user_input := st.chat_input("추가 질문 입력"):
                with st.chat_message("user"): st.markdown(user_input)
                st.session_state['chat_history'].append({"role": "user", "content": user_input})
                
                with st.chat_message("assistant"):
                    with st.spinner("생각 중..."):
                        try:
                            model = genai.GenerativeModel('gemini-flash-latest')
                            context = f"아파트: {target_row['아파트명']}, 가격: {target_row['매매가(억)']}억"
                            history = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state['chat_history'][-3:]])
                            final_prompt = f"{context}\n{history}\nUser: {user_input}\nAssistant:"
                            res = model.generate_content(final_prompt)
                            st.markdown(res.text)
                            st.session_state['chat_history'].append({"role": "assistant", "content": res.text})
                        except Exception as e: st.error(f"오류: {e}")
    else:
        st.info("👉 먼저 **[데이터 관리(수집)]** 탭에서 데이터를 수집해주세요.")

# ==========================================================================
# TAB 3: 데이터 관리 (수집)
# ==========================================================================
with tab3:
    st.header("⚙️ 데이터 수집 및 업데이트")
    st.info("모바일 우회 모드로 작동합니다. (PC에서도 실행 가능)")
    
    naver_regions = {
        "서울 강남구": "1168000000", "서울 서초구": "1165000000", "서울 송파구": "1171000000",
        "서울 용산구": "1117000000", "서울 성동구": "1120000000", "서울 마포구": "1144000000",
        "서울 영등포구": "1156000000", "서울 양천구": "1147000000", "서울 강동구": "1174000000", 
        "서울 금천구": "1154500000", "서울 구로구": "1153000000", "서울 관악구": "1162000000",
        "경기 성남 분당": "4113500000", "경기 과천": "4129000000", "경기 하남": "4145000000",
        "경기 안양 동안": "4117300000", "경기 수원 영통": "4111700000", "경기 광명": "4121000000"
    }
    
    targets = st.multiselect("업데이트할 지역 선택", list(naver_regions.keys()), default=["서울 강남구"])
    
    if st.button("🚀 네이버 호가 수집 및 DB 저장"):
        if not targets:
            st.error("지역을 선택해주세요.")
        else:
            progress = st.progress(0, text="수집 시작...")
            collected_data = []
            
            for i, region in enumerate(targets):
                progress.progress((i+1)/len(targets), text=f"[{region}] 모바일 접속 중...")
                df_res = get_naver_real_estate_data(naver_regions[region], region)
                if df_res is not None and not df_res.empty:
                    collected_data.append(df_res)
                # 너무 빠르면 모바일도 차단될 수 있으니 1초 대기
                time.sleep(1) 
                
            progress.empty()
            
            if collected_data:
                final_df = pd.concat(collected_data, ignore_index=True)
                try:
                    conn.update(data=final_df)
                    st.success(f"✅ 총 {len(final_df)}개 아파트 단지 데이터 저장 완료!")
                    st.dataframe(final_df.head())
                except Exception as e:
                    st.error(f"저장 실패: {e}")
            else:
                st.error("수집된 데이터가 없습니다. (URL 변경 또는 강력 차단 상태)")
                st.write("해결책: 잠시 후(10분 뒤) 다시 시도해보세요.")
