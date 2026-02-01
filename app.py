import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
from PublicDataReader import TransactionPrice as Transaction
from datetime import datetime, timedelta
import time

# --------------------------------------------------------------------------
# [1] 설정 및 초기화
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 자산 관리", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets or "PUBLIC_DATA_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: 키가 설정되지 않았습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
api_key = st.secrets["PUBLIC_DATA_KEY"]

st.title("🏙️ AI 부동산 통합 솔루션 (Personalized)")
st.caption("서울시 25개 자치구 전용 분석 도구")
st.markdown("---")

# --------------------------------------------------------------------------
# [2] 사이드바 (데이터 수집 & 내 자산 설정)
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 재정 상황 (Private)")
    with st.expander("💸 자산 및 소득 입력 (클릭)", expanded=True):
        user_cash = st.number_input("가용 현금 (억 원)", min_value=0.0, value=3.0, step=0.1)
        user_income = st.number_input("연 소득 (천만 원)", min_value=0.0, value=8.0, step=0.5)
        target_loan_rate = st.slider("예상 대출 금리 (%)", 2.0, 8.0, 4.0)
        
    st.divider()

    st.header("🔍 데이터 자동 수집")
    
    # [수정됨] 분당구 제거 -> 서울시 25개 자치구만 남김
    district_code = {
        "강남구": "11680", "강동구": "11740", "강북구": "11305", "강서구": "11500", "관악구": "11620",
        "광진구": "11215", "구로구": "11530", "금천구": "11545", "노원구": "11350", "도봉구": "11320",
        "동대문구": "11230", "동작구": "11590", "마포구": "11440", "서대문구": "11410", "서초구": "11650",
        "성동구": "11200", "성북구": "11290", "송파구": "11710", "양천구": "11470", "영등포구": "11560",
        "용산구": "11170", "은평구": "11380", "종로구": "11110", "중구": "11140", "중랑구": "11260"
    }
    
    district_options = ["전체 지역 (목록 전체)"] + sorted(list(district_code.keys()))
    selected_option = st.selectbox("수집할 지역(구)", district_options)
    
    if st.button("📥 실거래가(매매+전월세) 가져오기"):
        if selected_option == "전체 지역 (목록 전체)":
            target_districts = district_code
        else:
            target_districts = {selected_option: district_code[selected_option]}
            
        progress_bar = st.progress(0, text="데이터 수집 준비 중...")
        
        df_sales_list = []
        df_rent_list = []
        
        try:
            api = Transaction(api_key)
            now = datetime.now()
            # 최근 2개월치 데이터 조회
            months_to_fetch = [now.strftime("%Y%m"), (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")]
            
            total_steps = len(target_districts) * len(months_to_fetch) * 2
            current_step = 0
            
            for district_name, code in target_districts.items():
                # [1] 매매 데이터
                for month in months_to_fetch:
                    current_step += 1
                    progress_bar.progress(current_step / total_steps, text=f"[{district_name}] {month} 매매 데이터...")
                    try:
                        df_raw = api.get_data(property_type="아파트", trade_type="매매", sigungu_code=code, year_month=month)
                        if df_raw is not None and not df_raw.empty:
                            # 컬럼 청소 (공백, 괄호 제거)
                            df_raw.columns = df_raw.columns.str.strip().str.replace(r'\(.*\)', '', regex=True)
                            if '전용면적' in df_raw.columns:
                                df_raw['구'] = district_name 
                                df_sales_list.append(df_raw)
                    except: pass
                    time.sleep(0.1) # 서버 보호용 딜레이

                # [2] 전월세 데이터
                for month in months_to_fetch:
                    current_step += 1
                    progress_bar.progress(current_step / total_steps, text=f"[{district_name}] {month} 전월세 데이터...")
                    try:
                        df_raw_rent = api.get_data(property_type="아파트", trade_type="전월세", sigungu_code=code, year_month=month)
                        if df_raw_rent is not None and not df_raw_rent.empty:
                            df_raw_rent.columns = df_raw_rent.columns.str.strip().str.replace(r'\(.*\)', '', regex=True)
                            if '전용면적' in df_raw_rent.columns:
                                df_raw_rent['구'] = district_name
                                df_rent_list.append(df_raw_rent)
                    except: pass
                    time.sleep(0.1)

            progress_bar.empty()

            if df_sales_list:
                df_sales_all = pd.concat(df_sales_list, ignore_index=True)
                
                rent_map = {}
                if df_rent_list:
                    df_rent_all = pd.concat(df_rent_list, ignore_index=True)
                    for _, row in df_rent_all.iterrows():
                        try:
                            apt_name = row.get('단지명', row.get('단지', row.get('아파트', '')))
                            area = float(row['전용면적'])
                            pyung = round(area / 3.3, 1)
                            key = (apt_name, pyung)
                            
                            deposit = int(str(row.get('보증금액', '0')).replace(',', '')) / 10000 
                            monthly = int(str(row.get('월세금액', '0')).replace(',', ''))
                            
                            if key not in rent_map: rent_map[key] = {'전세': [], '월세보증금': [], '월세액': []}
                            if monthly == 0: rent_map[key]['전세'].append(deposit)
                            else: 
                                rent_map[key]['월세보증금'].append(deposit)
                                rent_map[key]['월세액'].append(monthly)
                        except: continue
                
                df_clean = pd.DataFrame()
                if '단지명' in df_sales_all.columns: apt_col = '단지명'
                elif '단지' in df_sales_all.columns: apt_col = '단지'
                else: apt_col = '아파트'
                
                df_clean['아파트명'] = df_sales_all[apt_col]
                
                if '구' in df_sales_all.columns:
                    df_clean['지역'] = df_sales_all['구'] + " " + df_sales_all['법정동']
                else:
                    df_clean['지역'] = df_sales_all['법정동']

                df_clean['평형'] = df_sales_all['전용면적'].astype(float).apply(lambda x: round(x / 3.3, 1))
                df_clean['매매가(억)'] = df_sales_all['거래금액'].astype(str).str.replace(',', '').astype(int) / 10000
                
                if '년' in df_sales_all.columns:
                    df_clean['거래일'] = df_sales_all['년'].astype(str) + "-" + df_sales_all['월'].astype(str).str.zfill(2) + "-" + df_sales_all['일'].astype(str).str.zfill(2)
                else:
                    df_clean['거래일'] = df_sales_all['계약년도'].astype(str) + "-" + df_sales_all['계약일'].astype(str)

                def match_rent(row):
                    key = (row['아파트명'], row['평형'])
                    jeonse = 0.0
                    deposit = 0.0
                    monthly_rent = 0
                    if key in rent_map:
                        data = rent_map[key]
                        if data['전세']: jeonse = sum(data['전세']) / len(data['전세'])
                        if data['월세보증금']:
                            deposit = sum(data['월세보증금']) / len(data['월세보증금'])
                            monthly_rent = sum(data['월세액']) / len(data['월세액'])
                    return pd.Series([jeonse, deposit, monthly_rent])

                df_clean[['전세가(억)', '월세보증금(억)', '월세액(만원)']] = df_clean.apply(match_rent, axis=1)
                df_clean['전고점(억)'] = 0.0
                df_clean['입지점수'] = 0
                df_clean = df_clean.sort_values(by='거래일', ascending=False)
                
                st.session_state['fetched_data'] = df_clean
                st.success(f"✅ 총 {len(df_clean)}건 수집 완료! (데이터가 안 보이면 내일 다시 시도하세요)")
            else:
                st.warning("⚠️ 데이터를 가져오지 못했습니다. (일일 트래픽 초과 가능성 높음)")
        except Exception as e:
            st.error(f"오류 발생: {e}")

# --------------------------------------------------------------------------
# [3] 메인 화면
# --------------------------------------------------------------------------
tab1, tab2 = st.tabs(["📥 데이터 확인 및 저장", "📊 통합 분석 & 랭킹"])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    pass

# --- TAB 1: 데이터 저장 ---
with tab1:
    st.subheader("📡 실시간 시세 (매매 + 전월세)")
    
    if 'fetched_data' in st.session_state:
        df_new = st.session_state['fetched_data']
        search_apt = st.text_input("아파트 검색", placeholder="예: 래미안")
        if search_apt:
            df_display = df_new[df_new['아파트명'].astype(str).str.contains(search_apt)]
        else:
            df_display = df_new
        
        st.dataframe(df_display.style.format({'매매가(억)': '{:.2f}', '전세가(억)': '{:.2f}', '전고점(억)': '{:.2f}'}))
        
        if st.button("💾 구글 시트에 저장 (기준정보 반영)"):
            status_container = st.container()
            try:
                # 1. 기준정보 로드
                try:
                    df_master = conn.read(worksheet="기준정보", ttl=0)
                    master_dict = {}
                    if not df_master.empty:
                        for _, row in df_master.iterrows():
                            # 공백 제거 매칭
                            raw_name = str(row['아파트명'])
                            clean_name = raw_name.replace(" ", "").strip()
                            master_dict[clean_name] = {'전고점': row.get('전고점(억)', 0), '점수': row.get('입지점수', 0)}
                except:
                    master_dict = {}

                # 2. 매칭
                for idx, row in df_new.iterrows():
                    target_name = str(row['아파트명']).replace(" ", "").strip()
                    if target_name in master_dict:
                        info = master_dict[target_name]
                        df_new.at[idx, '전고점(억)'] = info['전고점']
                        df_new.at[idx, '입지점수'] = info['점수']

                # 3. 저장
                try:
                    df_current = conn.read(ttl=0)
                except:
                    df_current = pd.DataFrame()

                cols = ['아파트명', '지역', '평형', '매매가(억)', '전세가(억)', '월세보증금(억)', '월세액(만원)', '전고점(억)', '입지점수']
                
                if df_current.empty:
                    final_df = df_new[cols].copy()
                else:
                    current_dict = {}
                    for _, row in df_current.iterrows():
                        k_name = str(row['아파트명']).replace(" ", "").strip()
                        k_pyung = str(row['평형'])
                        key = f"{k_name}_{k_pyung}"
                        current_dict[key] = row.to_dict()
                    
                    for _, row in df_new.iterrows():
                        k_name = str(row['아파트명']).replace(" ", "").strip()
                        k_pyung = str(row['평형'])
                        key = f"{k_name}_{k_pyung}"
                        if key in current_dict:
                            target = current_dict[key]
                            target['매매가(억)'] = row['매매가(억)']
                            target['전세가(억)'] = row['전세가(억)']
                            target['월세보증금(억)'] = row['월세보증금(억)']
                            target['월세액(만원)'] = row['월세액(만원)']
                            if row['전고점(억)'] > 0: target['전고점(억)'] = row['전고점(억)']
                            if row['입지점수'] > 0: target['입지점수'] = row['입지점수']
                        else:
                            current_dict[key] = row[cols].to_dict()
                    
                    final_df = pd.DataFrame(list(current_dict.values()))
                    final_df = final_df[cols]
                
                conn.update(data=final_df)
                st.balloons()
                st.success("✅ 저장 완료! 화면을 새로고침합니다.")
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")
    else:
        st.info("👈 왼쪽 사이드바에서 [실거래가 가져오기] 버튼을 눌러주세요.")

# --- TAB 2: 통합 분석 & 랭킹 ---
with tab2:
    st.header("🏆 AI 부동산 온라인 임장 (Ranking)")
    
    try:
        df_sheet = conn.read(ttl=0)
        
        if not df_sheet.empty:
            df_rank = df_sheet.copy()
            df_rank['하락률(%)'] = df_rank.apply(lambda x: ((x['전고점(억)'] - x['매매가(억)']) / x['전고점(억)'] * 100) if x['전고점(억)'] > 0 else 0, axis=1)
            df_rank['갭(억)'] = df_rank['매매가(억)'] - df_rank['전세가(억)']

            # [필터링 UI]
            with st.expander("🕵️‍♂️ 나에게 딱 맞는 아파트 찾기 (필터 설정)", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write("📐 **평형 선택**")
                    pyung_range = st.slider("원하는 평수 범위", 10, 80, (20, 40), step=1)
                    exclude_small = st.checkbox("도시형/소형 제외 (20평 미만 숨기기)", value=True)
                with c2:
                    st.write("💰 **매매가 예산**")
                    price_max = st.slider("최대 매매가 (억 원)", 5, 50, 20)
                with c3:
                    st.write("💸 **투자/전세 조건**")
                    gap_max = st.slider("최대 갭 투자금 (매매-전세)", 1, 20, 10)
            
            # [필터 적용]
            df_filtered = df_rank[
                (df_rank['평형'] >= pyung_range[0]) & 
                (df_rank['평형'] <= pyung_range[1])
            ]
            if exclude_small:
                df_filtered = df_filtered[df_filtered['평형'] >= 20]
            
            df_filtered = df_filtered[df_filtered['매매가(억)'] <= price_max]
            df_invest_filtered = df_filtered[df_filtered['갭(억)'] <= gap_max]

            st.divider()

            regions = ["전체"] + sorted(df_filtered['지역'].unique().tolist())
            selected_region_rank = st.selectbox("지역별로 모아보기", regions)
            
            if selected_region_rank != "전체":
                df_filtered = df_filtered[df_filtered['지역'] == selected_region_rank]
                df_invest_filtered = df_invest_filtered[df_invest_filtered['지역'] == selected_region_rank]
            
            col_r1, col_r2 = st.columns(2)
            
            with col_r1:
                st.subheader(f"🏡 실거주 추천 (총 {len(df_filtered)}개)")
                st.caption(f"예산 {price_max}억 이하, {pyung_range[0]}~{pyung_range[1]}평형 (저평가순)")
                if not df_filtered.empty:
                    df_living = df_filtered.sort_values(by=['하락률(%)', '입지점수'], ascending=[False, False]).head(10)
                    st.dataframe(df_living[['아파트명', '지역', '평형', '매매가(억)', '하락률(%)', '입지점수']].style.format({'매매가(억)': '{:.1f}', '하락률(%)': '{:.1f}%'}))
                else:
                    st.info("조건에 맞는 매물이 없습니다.")
                
            with col_r2:
                st.subheader(f"💰 갭투자 추천 (총 {len(df_invest_filtered)}개)")
                st.caption(f"투자금 {gap_max}억 이하 (갭 작은순)")
                df_invest_final = df_invest_filtered[df_invest_filtered['전세가(억)'] > 0]
                if not df_invest_final.empty:
                    df_invest = df_invest_final.sort_values(by=['갭(억)', '입지점수'], ascending=[True, False]).head(10)
                    st.dataframe(df_invest[['아파트명', '지역', '평형', '매매가(억)', '전세가(억)', '갭(억)']].style.format({'매매가(억)': '{:.1f}', '전세가(억)': '{:.1f}', '갭(억)': '{:.1f}'}))
                else:
                    st.info("조건에 맞는 갭투자 매물이 없습니다.")

            st.divider()

            st.subheader("🤖 나만의 AI 부동산 투자 자문")
            apt_list = df_sheet['아파트명'].unique().tolist()
            selected_apt = st.selectbox("분석할 단지를 검색하세요", apt_list, index=None, placeholder="아파트명을 입력하세요...")
            
            if selected_apt:
                target = df_sheet[df_sheet['아파트명'] == selected_apt].iloc[0]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("아파트명", target['아파트명'])
                c2.metric("현재 매매가", f"{target['매매가(억)']}억")
                c3.metric("전세가율", f"{(target['전세가(억)']/target['매매가(억)']*100 if target['매매가(억)']>0 else 0):.1f}%")
                c4.metric("내 가용현금", f"{user_cash}억")

                if st.button("🚀 이 아파트 심층 분석 & 매수 가능성 진단"):
                    loan_needed = target['매매가(억)'] - user_cash
                    yearly_interest = loan_needed * (target_loan_rate / 100)
                    dsr_rough = (yearly_interest / (user_income/10)) * 100 if user_income > 0 else 0
                    
                    prompt = f"""
                    당신은 냉철한 부동산 전문가입니다. 사용자의 재정 상황을 고려하여 매수 적정성을 판단해주세요.
                    [매물] {target['아파트명']} ({target['지역']}), {target['평형']}평, 매매 {target['매매가(억)']}억, 전세 {target['전세가(억)']}억, 전고점 {target['전고점(억)']}억
                    [재정] 현금 {user_cash}억, 연소득 {user_income}천만, 금리 {target_loan_rate}%, 예상 DSR {dsr_rough:.1f}%
                    [요청] 자금여력, 가격적정성, 투자/실거주 적합성, 최종결론(매수/보류/매도)을 마크다운으로 작성.
                    """
                    with st.spinner("AI 분석 중..."):
                        try:
                            model = genai.GenerativeModel('gemini-flash-latest')
                            res = model.generate_content(prompt)
                            st.markdown(res.text)
                        except Exception as e:
                            st.error(f"AI 호출 실패: {e}")
        else:
            st.info("데이터베이스가 비어있습니다. [데이터 확인 및 저장] 탭에서 데이터를 먼저 수집해주세요.")
    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
