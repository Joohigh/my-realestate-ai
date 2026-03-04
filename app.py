import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
from datetime import datetime, timedelta
from urllib.parse import unquote
import time

# --------------------------------------------------------------------------
# [1] 설정 및 초기화
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 자산 관리", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets or "PUBLIC_DATA_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: 키가 설정되지 않았습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
api_key_decoded = unquote(st.secrets["PUBLIC_DATA_KEY"])

st.title("🏙️ AI 부동산 통합 솔루션 (Ultimate Ver.)")
st.caption("실거래가 정밀 분석 + AI 자문 + 🌟 [NEW] 맞춤형 청약 컨설팅 & 챗봇")
st.markdown("---")

# --------------------------------------------------------------------------
# [함수] 정부 서버 직접 접속 (매매 & 전월세 & 청약)
# --------------------------------------------------------------------------
def fetch_trade_data(lawd_cd, deal_ymd, service_key):
    url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    params = {"serviceKey": service_key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd, "numOfRows": 1000, "pageNo": 1}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            if root.findtext(".//resultCode") in ["00", "000"]:
                items = root.findall(".//item")
                data_list = []
                for item in items:
                    data_list.append({
                        "아파트": item.findtext("아파트") or item.findtext("aptNm") or "",
                        "전용면적": item.findtext("전용면적") or item.findtext("excluUseAr") or "0",
                        "거래금액": item.findtext("거래금액") or item.findtext("dealAmount") or "0",
                        "층": item.findtext("층") or item.findtext("floor") or "",
                        "건축년도": item.findtext("건축년도") or item.findtext("buildYear") or "",
                        "법정동": item.findtext("법정동") or item.findtext("umdNm") or "",
                        "년": (item.findtext("년") or item.findtext("dealYear") or "").strip(),
                        "월": (item.findtext("월") or item.findtext("dealMonth") or "").strip(),
                        "일": (item.findtext("일") or item.findtext("dealDay") or "").strip(),
                    })
                return pd.DataFrame(data_list)
    except: return None
    return None

def fetch_rent_data(lawd_cd, deal_ymd, service_key):
    url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
    params = {"serviceKey": service_key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd, "numOfRows": 1000, "pageNo": 1}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            if root.findtext(".//resultCode") in ["00", "000"]:
                items = root.findall(".//item")
                data_list = []
                for item in items:
                    data_list.append({
                        "아파트": item.findtext("아파트") or item.findtext("aptNm") or "",
                        "전용면적": item.findtext("전용면적") or item.findtext("excluUseAr") or "0",
                        "보증금액": item.findtext("보증금액") or item.findtext("deposit") or "0",
                        "월세금액": item.findtext("월세금액") or item.findtext("monthlyRent") or "0",
                    })
                return pd.DataFrame(data_list)
    except: return None
    return None

def fetch_applyhome_data(service_key):
    url = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
    params = {"page": 1, "perPage": 100, "serviceKey": service_key}
    try:
        response = requests.get(url, params=params, verify=False, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "data" in data:
                df = pd.DataFrame(data["data"])
                if not df.empty:
                    df_seoul = df[df['SUBSCRPT_AREA_CODE_NM'].astype(str).str.contains('서울', na=False)]
                    if df_seoul.empty: return pd.DataFrame()
                        
                    res_df = pd.DataFrame()
                    res_df['아파트명(청약단지)'] = df_seoul['HOUSE_NM']
                    res_df['지역(공급위치)'] = df_seoul['HSSPLY_ADRES']
                    res_df['공급규모(세대)'] = df_seoul['TOT_SUPLY_HSHLDCO']
                    res_df['모집공고일'] = df_seoul['RCRIT_PBLANC_DE']
                    res_df['청약시작일'] = df_seoul['RCEPT_BGNDE']
                    res_df['청약종료일'] = df_seoul['RCEPT_ENDDE']
                    res_df['당첨자발표일'] = df_seoul['PRZWNER_PRESNATN_DE']
                    
                    res_df = res_df.sort_values('모집공고일', ascending=False)
                    return res_df
    except: return None
    return pd.DataFrame()

# --------------------------------------------------------------------------
# [2] 사이드바
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 재정 및 청약 조건")
    
    with st.expander("💸 자산 및 소득 (클릭)", expanded=True):
        user_cash = st.number_input("가용 현금 (억 원)", min_value=0.0, value=3.0, step=0.1)
        user_income = st.number_input("연 소득 (천만 원)", min_value=0.0, value=5.0, step=0.5)
        target_loan_rate = st.slider("예상 대출 금리 (%)", 2.0, 8.0, 4.0)
        
    with st.expander("📝 청약 가점 및 특별공급 조건 (클릭)", expanded=True):
        is_homeless = st.checkbox("무주택자", value=True)
        homeless_years = st.number_input("무주택 기간 (년)", 0, 30, 5)
        is_newlywed = st.checkbox("신혼부부 (혼인기간 7년 이내)", value=False)
        is_first_time = st.checkbox("생애최초 (세대원 전원 주택소유 이력 없음)", value=False)
        children_count = st.number_input("미성년 자녀 수 (명)", 0, 10, 0)
        sub_account_years = st.number_input("청약통장 가입기간 (년)", 0, 30, 5)

    st.divider()

    st.header("🔍 실거래가 자동 수집")
    district_code = {
        "서울 강남구": "11680", "서울 강동구": "11740", "서울 강북구": "11305", "서울 강서구": "11500", "서울 관악구": "11620",
        "서울 광진구": "11215", "서울 구로구": "11530", "서울 금천구": "11545", "서울 노원구": "11350", "서울 도봉구": "11320",
        "서울 동대문구": "11230", "서울 동작구": "11590", "서울 마포구": "11440", "서울 서대문구": "11410", "서울 서초구": "11650",
        "서울 성동구": "11200", "서울 성북구": "11290", "서울 송파구": "11710", "서울 양천구": "11470", "서울 영등포구": "11560",
        "서울 용산구": "11170", "서울 은평구": "11380", "서울 종로구": "11110", "서울 중구": "11140", "서울 중랑구": "11260",
        "경기 과천시": "41290", "경기 광명시": "41210", "경기 하남시": "41450", 
        "경기 성남 분당": "41135", "경기 성남 수정": "41131", "경기 성남 중원": "41133",
        "경기 안양 동안": "41173", "경기 안양 만안": "41171",
        "경기 수원 영통": "41117", "경기 수원 팔달": "41115",
        "경기 용인 수지": "41465", "경기 용인 기흥": "41463",
        "경기 고양 일산동": "41285", "경기 고양 일산서": "41287", "경기 고양 덕양": "41281",
        "경기 화성시": "41590", "경기 김포시": "41570", "경기 남양주시": "41360",
        "경기 구리시": "41310", "경기 부천시": "41190", "경기 군포시": "41410", "경기 의왕시": "41430"
    }
    district_options = ["전체 지역 (목록 전체)"] + sorted(list(district_code.keys()))
    selected_option = st.selectbox("수집할 지역(구)", district_options)
    
    if st.button("📥 실거래가 싹 가져오기"):
        target_districts = district_code if selected_option == "전체 지역 (목록 전체)" else {selected_option: district_code[selected_option]}
        progress_bar = st.progress(0, text="정부 서버 연결 중...")
        
        df_trade_list = []
        df_rent_list = []
        now = datetime.now()
        months = [now.strftime("%Y%m"), (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")]
        
        total = len(target_districts) * len(months) * 2
        step = 0
        
        for name, code in target_districts.items():
            for ym in months:
                step += 1
                progress_bar.progress(step / total, text=f"[{name}] {ym} 매매 수신 중...")
                df_raw_trade = fetch_trade_data(code, ym, api_key_decoded)
                if df_raw_trade is not None and not df_raw_trade.empty:
                    df_raw_trade['구'] = name
                    df_trade_list.append(df_raw_trade)
                time.sleep(0.1)

                step += 1
                progress_bar.progress(step / total, text=f"[{name}] {ym} 전월세 수신 중...")
                df_raw_rent = fetch_rent_data(code, ym, api_key_decoded)
                if df_raw_rent is not None and not df_raw_rent.empty:
                    df_rent_list.append(df_raw_rent)
                time.sleep(0.1)
        
        progress_bar.empty()
        
        if df_trade_list:
            df_all_trade = pd.concat(df_trade_list, ignore_index=True)
            df_clean = pd.DataFrame()
            df_clean['아파트명'] = df_all_trade['아파트']
            df_clean['지역'] = df_all_trade['구'] + " " + df_all_trade['법정동']
            df_clean['평형'] = pd.to_numeric(df_all_trade['전용면적'], errors='coerce').fillna(0).apply(lambda x: round(x / 3.3, 1))
            df_clean['층'] = df_all_trade['층']
            df_clean['건축년도'] = df_all_trade['건축년도']
            df_clean['매매가(억)'] = pd.to_numeric(df_all_trade['거래금액'].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0).astype(int) / 10000
            
            df_clean['년'] = df_all_trade['년'].astype(str).str.zfill(4)
            df_clean['월'] = df_all_trade['월'].astype(str).str.zfill(2)
            df_clean['일'] = df_all_trade['일'].astype(str).str.zfill(2)
            df_clean['거래일'] = df_clean.apply(lambda x: f"{x['년']}-{x['월']}-{x['일']}" if x['년'] != '0000' else now.strftime("%Y-%m-%d"), axis=1)
            
            df_clean['조인키_아파트'] = df_clean['아파트명'].astype(str).str.replace(' ', '')
            df_clean['조인키_평형'] = df_clean['평형'].apply(lambda x: round(x))
            
            if df_rent_list:
                df_all_rent = pd.concat(df_rent_list, ignore_index=True)
                df_all_rent['평형'] = pd.to_numeric(df_all_rent['전용면적'], errors='coerce').fillna(0).apply(lambda x: round(x / 3.3, 1))
                df_all_rent['보증금(억)'] = pd.to_numeric(df_all_rent['보증금액'].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0).astype(int) / 10000
                df_all_rent['월세(만)'] = pd.to_numeric(df_all_rent['월세금액'].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0).astype(int)
                
                df_all_rent['조인키_아파트'] = df_all_rent['아파트'].astype(str).str.replace(' ', '')
                df_all_rent['조인키_평형'] = df_all_rent['평형'].apply(lambda x: round(x))
                
                df_jeonse = df_all_rent[df_all_rent['월세(만)'] == 0]
                df_monthly = df_all_rent[df_all_rent['월세(만)'] > 0]
                
                jeonse_avg = df_jeonse.groupby(['조인키_아파트', '조인키_평형'])['보증금(억)'].mean().reset_index()
                jeonse_avg.rename(columns={'보증금(억)': '평균전세가(억)'}, inplace=True)
                
                monthly_avg = df_monthly.groupby(['조인키_아파트', '조인키_평형'])[['보증금(억)', '월세(만)']].mean().reset_index()
                monthly_avg.rename(columns={'보증금(억)': '평균월세보증금(억)', '월세(만)': '평균월세액(만)'}, inplace=True)
                
                df_clean = pd.merge(df_clean, jeonse_avg, how='left', on=['조인키_아파트', '조인키_평형'])
                df_clean = pd.merge(df_clean, monthly_avg, how='left', on=['조인키_아파트', '조인키_평형'])
                
                df_clean['전세가(억)'] = df_clean['평균전세가(억)'].fillna(df_clean['매매가(억)'] * 0.6)
                df_clean['월세보증금(억)'] = df_clean['평균월세보증금(억)'].fillna(0)
                df_clean['월세액(만원)'] = df_clean['평균월세액(만)'].fillna(0)
            else:
                df_clean['전세가(억)'] = df_clean['매매가(억)'] * 0.6
                df_clean['월세보증금(억)'] = 0
                df_clean['월세액(만원)'] = 0

            df_clean['전고점(억)'] = 0.0
            df_clean['입지점수'] = 0
            df_clean = df_clean.sort_values(by='거래일', ascending=False)
            
            cols_to_keep = ['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '전세가(억)', '월세보증금(억)', '월세액(만원)', '거래일', '전고점(억)', '입지점수']
            st.session_state['fetched_data'] = df_clean[cols_to_keep]
            st.success(f"✅ 총 {len(df_clean)}건 수집 완료!")
        else:
            st.warning("⚠️ 수집된 데이터가 없습니다.")

# --------------------------------------------------------------------------
# [3] 메인 화면 (3개 탭으로 구성)
# --------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📥 실거래가 저장", "🏆 랭킹 & 💬 매매 자문", "📅 서울 청약 & 💬 청약 자문"])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    pass

# --- TAB 1: 데이터 저장 ---
with tab1:
    st.subheader("📡 실시간 실거래 시세 (매매+전세)")
    if 'fetched_data' in st.session_state:
        df_new = st.session_state['fetched_data']
        search_apt = st.text_input("아파트 검색", placeholder="예: 래미안")
        df_display = df_new[df_new['아파트명'].astype(str).str.contains(search_apt)] if search_apt else df_new
        st.dataframe(df_display.style.format({'매매가(억)': '{:.2f}', '전세가(억)': '{:.2f}', '월세보증금(억)': '{:.2f}'}))
        
        if st.button("💾 구글 시트에 저장 (기준정보 반영)"):
            try:
                try:
                    df_master = conn.read(worksheet="기준정보", ttl=0)
                    master_dict = {str(row['아파트명']).replace(" ", "").strip(): {'전고점': row.get('전고점(억)', 0), '점수': row.get('입지점수', 0)} for _, row in df_master.iterrows()}
                except: master_dict = {}

                for idx, row in df_new.iterrows():
                    name = str(row['아파트명']).replace(" ", "").strip()
                    if name in master_dict:
                        df_new.at[idx, '전고점(억)'] = master_dict[name]['전고점']
                        df_new.at[idx, '입지점수'] = master_dict[name]['점수']

                try: df_current = conn.read(ttl=0)
                except: df_current = pd.DataFrame()

                cols = ['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '전세가(억)', '월세보증금(억)', '월세액(만원)', '거래일', '전고점(억)', '입지점수']
                
                if not df_current.empty:
                    for c in ['층', '건축년도', '거래일']:
                        if c not in df_current.columns: df_current[c] = "-"

                if df_current.empty: final_df = df_new[cols].copy()
                else:
                    current_dict = {f"{str(r['아파트명']).replace(' ', '').strip()}_{r['평형']}": r.to_dict() for _, r in df_current.iterrows()}
                    for _, row in df_new.iterrows():
                        key = f"{str(row['아파트명']).replace(' ', '').strip()}_{row['평형']}"
                        if key in current_dict:
                            current_dict[key].update({
                                '매매가(억)': row['매매가(억)'], '층': row['층'], '건축년도': row['건축년도'], 
                                '전세가(억)': row['전세가(억)'], '월세보증금(억)': row['월세보증금(억)'],
                                '월세액(만원)': row['월세액(만원)'], '거래일': row['거래일']
                            })
                            if row['전고점(억)'] > 0: current_dict[key]['전고점(억)'] = row['전고점(억)']
                            if row['입지점수'] > 0: current_dict[key]['입지점수'] = row['입지점수']
                        else: current_dict[key] = row[cols].to_dict()
                    final_df = pd.DataFrame(list(current_dict.values()))[cols]
                
                conn.update(data=final_df)
                st.balloons()
                st.success("✅ 저장 완료! 데이터가 시트에 업데이트되었습니다.")
                time.sleep(1)
                st.rerun()
            except Exception as e: st.error(f"저장 실패: {e}")
    else: st.info("👈 왼쪽 사이드바에서 [실거래가 가져오기] 버튼을 눌러주세요.")

# --- TAB 2: 매매 분석 (랭킹 + AI 대화) ---
with tab2:
    try:
        df_sheet = conn.read(ttl=0)
        
        if not df_sheet.empty and '매매가(억)' in df_sheet.columns:
            for c in ['층', '건축년도']:
                if c not in df_sheet.columns: df_sheet[c] = "-"

            st.header("🏆 AI 추천 랭킹 (매매/전세)")
            
            df_rank = df_sheet.copy()
            df_rank['하락률(%)'] = df_rank.apply(lambda x: ((x['전고점(억)'] - x['매매가(억)']) / x['전고점(억)'] * 100) if x.get('전고점(억)', 0) > 0 else 0, axis=1)
            df_rank['갭(억)'] = df_rank['매매가(억)'] - df_rank['전세가(억)']

            with st.expander("🕵️‍♂️ 조건 설정 (필터 펼치기)", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write("📐 평형 선택")
                    pyung_range = st.slider("원하는 평수", 10, 80, (20, 40), step=1)
                    exclude_small = st.checkbox("20평 미만 제외", value=True)
                with c2:
                    st.write("💰 매매가 예산")
                    price_max = st.slider("최대 매매가 (억)", 5, 50, 20)
                with c3:
                    st.write("💸 투자/전세 조건")
                    gap_max = st.slider("최대 갭 투자금 (억)", 1, 20, 10)

            df_filtered = df_rank[(df_rank['평형'] >= pyung_range[0]) & (df_rank['평형'] <= pyung_range[1])]
            if exclude_small: df_filtered = df_filtered[df_filtered['평형'] >= 20]
            df_filtered = df_filtered[df_filtered['매매가(억)'] <= price_max]
            df_invest_filtered = df_filtered[df_filtered['갭(억)'] <= gap_max]

            regions = ["전체"] + sorted(df_filtered['지역'].astype(str).unique().tolist())
            selected_region_rank = st.selectbox("지역별 필터", regions)
            if selected_region_rank != "전체":
                df_filtered = df_filtered[df_filtered['지역'] == selected_region_rank]
                df_invest_filtered = df_invest_filtered[df_invest_filtered['지역'] == selected_region_rank]

            col_r1, col_r2 = st.columns(2)
            with col_r1:
                st.subheader(f"🏡 실거주 추천 ({len(df_filtered)}건)")
                st.caption("저평가(하락률) + 입지점수 순")
                if not df_filtered.empty:
                    st.dataframe(
                        df_filtered.sort_values(by=['하락률(%)', '입지점수'], ascending=[False, False])[['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '하락률(%)']].style.format({'매매가(억)': '{:.1f}', '하락률(%)': '{:.1f}%'}),
                        height=500, use_container_width=True
                    )
                else: st.info("조건에 맞는 매물이 없습니다.")
            
            with col_r2:
                st.subheader(f"💰 갭투자 추천 ({len(df_invest_filtered)}건)")
                st.caption("적은 투자금(실제 갭) + 입지점수 순")
                if not df_invest_filtered.empty:
                    st.dataframe(
                        df_invest_filtered.sort_values(by=['갭(억)', '입지점수'], ascending=[True, False])[['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '갭(억)']].style.format({'매매가(억)': '{:.1f}', '갭(억)': '{:.1f}'}),
                        height=500, use_container_width=True
                    )
                else: st.info("조건에 맞는 갭투자 매물이 없습니다.")

            st.divider()

            st.header("💬 AI 매매/갭투자 자문 (Chat)")
            st.info("위 리스트에서 관심 있는 아파트를 검색해 AI와 상담해보세요.")

            df_sheet['선택키'] = df_sheet['지역'] + " " + df_sheet['아파트명'] + " (" + df_sheet['건축년도'].astype(str) + "년식, " + df_sheet['평형'].astype(str) + "평)"
            apt_list = sorted(df_sheet['선택키'].dropna().unique().tolist())
            
            selected_key = st.selectbox("상담할 매물 검색", apt_list, index=None, placeholder="예: 강남구 은마...")
            
            if 'last_selected_apt_tab2' not in st.session_state: st.session_state['last_selected_apt_tab2'] = None
            if selected_key != st.session_state['last_selected_apt_tab2']:
                st.session_state['messages_tab2'] = []
                st.session_state['last_selected_apt_tab2'] = selected_key
                st.session_state['context_prompt_tab2'] = ""

            if selected_key:
                target = df_sheet[df_sheet['선택키'] == selected_key].iloc[0]
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("아파트 스펙", f"{target.get('건축년도','-')}년식 ({target.get('층','-')}층)")
                c2.metric("실거래가", f"{target['매매가(억)']}억")
                c3.metric("실제 전세가", f"{target['전세가(억)']:.2f}억")
                c4.metric("입지점수", f"{target.get('입지점수', 0)}점")

                if st.button("🚀 매매 심층 분석 시작", type="primary"):
                    loan_needed = target['매매가(억)'] - user_cash
                    dsr_rough = (loan_needed * (target_loan_rate / 100)) / (user_income/10) * 100 if user_income > 0 else 0
                    
                    system_prompt = f"""
                    너는 최고의 부동산 투자 전문가야. 아래 팩트(국토부 실거래가)를 바탕으로 사용자와 대화해줘.
                    [매물] {target['아파트명']} ({target['지역']}), {target.get('건축년도','-')}년 건축, {target.get('층','-')}층, {target['평형']}평
                    [가격] 최근 매매가 {target['매매가(억)']}억, 최근 평균 전세가 {target['전세가(억)']:.2f}억, 전고점 {target.get('전고점(억)', 0)}억
                    [재정] 현금 {user_cash}억, 연소득 {user_income}천만, 금리 {target_loan_rate}%, 예상 DSR {dsr_rough:.1f}%
                    
                    먼저 이 매물의 가격 적정성, '층'과 '건축년도'를 고려한 투자/실거주 적합성, 자금 여력을 종합 분석해줘.
                    """
                    st.session_state['context_prompt_tab2'] = system_prompt
                    
                    with st.spinner("AI가 입체적으로 분석 중입니다..."):
                        try:
                            model = genai.GenerativeModel('gemini-flash-latest')
                            response = model.generate_content(system_prompt)
                            st.session_state['messages_tab2'].append({"role": "assistant", "content": response.text})
                            st.rerun()
                        except Exception as e: st.error(f"AI 호출 오류: {e}")

                for msg in st.session_state.get('messages_tab2', []):
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input("매매/갭투자 관련 질문을 입력하세요 (예: 1층인데 나중에 팔기 어려울까?)"):
                    with st.chat_message("user"): st.markdown(prompt)
                    st.session_state['messages_tab2'].append({"role": "user", "content": prompt})
                    
                    with st.chat_message("assistant"):
                        message_placeholder = st.empty()
                        try:
                            # [핵심 버그 수정 적용 완료] - 이전 대화 내역 병합 로직
                            model = genai.GenerativeModel('gemini-flash-latest')
                            if 'context_prompt_tab2' in st.session_state and st.session_state['context_prompt_tab2']:
                                history_text = ""
                                for m in st.session_state['messages_tab2'][:-1]:
                                    role_name = "사용자" if m['role'] == 'user' else "AI"
                                    history_text += f"{role_name}: {m['content']}\n"
                                
                                final_prompt = f"{st.session_state['context_prompt_tab2']}\n\n[이전 대화 내역]\n{history_text}\n\n사용자 질문: {prompt}"
                            else: 
                                final_prompt = prompt
                            
                            response = model.generate_content(final_prompt)
                            message_placeholder.markdown(response.text)
                            st.session_state['messages_tab2'].append({"role": "assistant", "content": response.text})
                        except Exception as e: message_placeholder.error(f"오류: {e}")
            else: st.info("👆 분석할 매물을 선택해주세요.")
                
        else: st.warning("데이터가 없습니다. [📥 실거래가 저장] 탭에서 데이터를 수집해주세요.")
    except Exception as e: st.error(f"오류: {e}")

# --- TAB 3: 서울 청약 일정 및 자문 (NEW) ---
with tab3:
    st.header("📅 서울 아파트 청약 (분양) 추천 및 컨설팅")
    st.info("💡 공공데이터포털 연동: 실시간 청약 공고 조회 및 내 가점/재정에 맞춘 전략 컨설팅")
    st.caption("※ 참고: 정부 API 정책상 정확한 주택형별 '분양가'는 노출되지 않으며, 상세 내역은 청약홈 홈페이지에서 확인하셔야 합니다.")
    
    if st.button("🔄 최신 서울 청약 일정 불러오기", type="primary"):
        with st.spinner("청약홈 서버에서 서울 지역 공고를 가져오는 중입니다..."):
            df_apply = fetch_applyhome_data(api_key_decoded)
            st.session_state['apply_data'] = df_apply
            # 새로운 청약을 불러오면 기존 채팅 내역 초기화
            st.session_state['messages_tab3'] = []
            
    if 'apply_data' in st.session_state:
        df_apply = st.session_state['apply_data']
        
        if df_apply is not None and not df_apply.empty:
            st.success(f"✅ 총 {len(df_apply)}건의 진행/예정 중인 서울 청약 공고를 찾았습니다!")
            st.dataframe(df_apply, use_container_width=True, hide_index=True)
            
            st.divider()
            
            st.subheader("🤖 AI 청약 맞춤형 분석 및 전략 추천")
            st.write("사이드바에 설정된 재정 상황 및 청약 가점 조건을 바탕으로 나만의 최적 청약 전략을 분석해 드립니다.")
            
            if 'messages_tab3' not in st.session_state: st.session_state['messages_tab3'] = []

            if st.button("✨ 내 조건에 맞는 단지 추천 및 자문 시작", type="primary"):
                apply_summary = df_apply[['아파트명(청약단지)', '지역(공급위치)', '공급규모(세대)', '청약시작일']].to_string(index=False)
                
                homeless_str = f"무주택 {homeless_years}년" if is_homeless else "유주택자"
                newlywed_str = "신혼부부(O)" if is_newlywed else "신혼부부(X)"
                first_time_str = "생애최초(O)" if is_first_time else "생애최초(X)"
                
                system_prompt_tab3 = f"""
                너는 대한민국의 최고 부동산 청약 및 특별공급 전문가야.
                
                [현재 청약 공고 리스트]
                {apply_summary}
                
                [사용자 재정 및 청약 스펙]
                - 자본: 현금 {user_cash}억 원 / 연소득 {user_income}천만 원 / 대출금리 {target_loan_rate}%
                - 주택소유: {homeless_str}
                - 특공조건: {newlywed_str} / {first_time_str} / 미성년 자녀 수 {children_count}명
                - 청약통장: 가입기간 {sub_account_years}년
                
                위 정보를 바탕으로 아래 리포트를 작성하고, 이후 사용자의 질문에 답해줘:
                1. 맞춤형 전략 추천: 사용자의 신혼/다자녀/무주택 조건에 비추어 볼 때 가점제, 추첨제, 혹은 특정 특별공급(신혼/다자녀/생애최초 등) 중 어느 전형으로 넣는 것이 당첨 확률이 가장 높은지 구체적으로 조언해줘.
                2. 추천 단지 BEST 1~2곳: 위 리스트 중에서 사용자의 당첨 가능성과 투자 가치가 가장 높은 단지를 짚어줘.
                3. 자금 조달 시나리오: 계약금-중도금-잔금 스케줄을 가정하여, 현재 현금({user_cash}억)과 연소득({user_income}천만)으로 무리 없이 분양을 받을 수 있는지 자금 흐름을 시뮬레이션해줘.
                """
                st.session_state['context_prompt_tab3'] = system_prompt_tab3
                
                with st.spinner("AI가 청약 자격(가점/특공)과 자금 조달 시나리오를 정밀 분석 중입니다..."):
                    try:
                        model = genai.GenerativeModel('gemini-flash-latest')
                        response = model.generate_content(system_prompt_tab3)
                        st.session_state['messages_tab3'] = [{"role": "assistant", "content": response.text}]
                        st.rerun()
                    except Exception as e:
                        st.error(f"AI 호출 오류: {e}")

            # 채팅 히스토리 출력
            for msg in st.session_state.get('messages_tab3', []):
                with st.chat_message(msg['role']): st.markdown(msg['content'])

            # 사용자의 후속 질문 입력기
            if prompt := st.chat_input("청약 전략, 대출, 특별공급 자격 등에 대해 자유롭게 질문하세요!"):
                with st.chat_message("user"): st.markdown(prompt)
                st.session_state['messages_tab3'].append({"role": "user", "content": prompt})
                
                with st.chat_message("assistant"):
                    message_placeholder = st.empty()
                    try:
                        # [핵심 버그 수정 적용 완료] - 이전 대화 내역 병합 로직
                        model = genai.GenerativeModel('gemini-flash-latest')
                        if 'context_prompt_tab3' in st.session_state and st.session_state['context_prompt_tab3']:
                            history_text = ""
                            for m in st.session_state['messages_tab3'][:-1]:
                                role_name = "사용자" if m['role'] == 'user' else "AI"
                                history_text += f"{role_name}: {m['content']}\n"
                            
                            final_prompt = f"{st.session_state['context_prompt_tab3']}\n\n[이전 대화 내역]\n{history_text}\n\n사용자 질문: {prompt}"
                        else:
                            final_prompt = prompt
                        
                        response = model.generate_content(final_prompt)
                        message_placeholder.markdown(response.text)
                        st.session_state['messages_tab3'].append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        message_placeholder.error(f"오류: {e}")
                        
        elif df_apply is not None and df_apply.empty:
            st.warning("현재 진행 중이거나 예정된 서울 지역 아파트 청약 공고가 없습니다.")
        else:
            st.error("🚨 청약 데이터를 불러오지 못했습니다. 공공데이터포털에서 활용 신청을 확인해주세요.")
