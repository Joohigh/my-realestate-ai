import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions  # [CHANGED] 429(ResourceExhausted) 분기 처리용
from datetime import datetime, timedelta
from urllib.parse import unquote
import time
import random  # [CHANGED] 지수 백오프 지터(jitter)용

# --------------------------------------------------------------------------
# [1] 설정 및 초기화
# --------------------------------------------------------------------------
st.set_page_config(page_title="AI 부동산 자산 관리", layout="wide")

if "GOOGLE_API_KEY" not in st.secrets or "PUBLIC_DATA_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 오류: 키가 설정되지 않았습니다.")
    st.stop()

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# [CHANGED] 별칭(gemini-flash-latest)은 구글이 가리키는 실제 모델이 바뀌면 할당량 정책도 흔들린다.
# 명시적 버전을 고정해 동작과 한도를 예측 가능하게 한다. (필요 시 secrets로 오버라이드)
GEMINI_MODEL = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")

# [CHANGED] AI 호출 동작을 제어하는 설정 상수 (매직 넘버/스트링 제거)
SEARCH_TOOL = "google_search_retrieval"
# 후속 질문에 검색 도구를 켤지 결정하는 트리거 키워드. 단순 질문에는 검색을 끄고 토큰을 아낀다.
SEARCH_TRIGGERS = ("비교", "시세", "호재", "학군", "최신", "뉴스", "경쟁률", "분양가", "주변", "단지")
# 후속 질문 시 프롬프트에 포함할 최대 대화 턴 수. 이력 윈도잉으로 TPM 폭증을 막는다.
MAX_HISTORY_TURNS = 6
# 429 재시도 정책
GEMINI_MAX_RETRIES = 4

api_key_decoded = unquote(st.secrets["PUBLIC_DATA_KEY"])

# R-ONE(한국부동산원) API 키는 별도 신청이 필요할 수 있음.
reb_api_key = unquote(st.secrets.get("REB_API_KEY", st.secrets["PUBLIC_DATA_KEY"]))

st.title("🏙️ AI 부동산 통합 솔루션 (Ultimate Ver. 2.1)")
st.caption("실거래가 + R-ONE 지수 시세 + AI 실시간 검색 자문 + 청약 컨설팅")
st.markdown("---")

# --------------------------------------------------------------------------
# [CHANGED] [함수 그룹 0] Gemini 호출 통합 헬퍼 (429 재시도 + 조건부 검색 + 이력 윈도잉)
#   - 세 탭에 흩어져 있던 모델 생성/검색도구/이력병합/예외처리를 단일 진입점으로 통합한다.
# --------------------------------------------------------------------------
def _build_model(use_search: bool):
    """검색 도구 사용 여부에 따라 모델 인스턴스를 생성한다."""
    if use_search:
        return genai.GenerativeModel(GEMINI_MODEL, tools=SEARCH_TOOL)
    return genai.GenerativeModel(GEMINI_MODEL)


def _should_use_search(text: str) -> bool:
    """질문에 외부 검색이 필요한 키워드가 포함되어 있는지 판정한다."""
    return any(keyword in text for keyword in SEARCH_TRIGGERS)


def _build_followup_prompt(context_prompt: str, messages: list, user_question: str) -> str:
    """
    후속 질문 프롬프트를 구성한다.
    전체 이력이 아니라 최근 MAX_HISTORY_TURNS개만 포함해 토큰(TPM) 소모를 제한한다.
    messages는 마지막 항목(방금 추가된 사용자 질문)을 제외하고 슬라이싱한다.
    """
    recent = messages[:-1][-MAX_HISTORY_TURNS:] if len(messages) > 1 else []
    history_text = "".join(
        f"{'사용자' if m['role'] == 'user' else 'AI'}: {m['content']}\n" for m in recent
    )
    return (
        f"{context_prompt}\n\n[최근 대화 내역]\n{history_text}\n\n사용자 질문: {user_question}"
    )


def ask_gemini(prompt: str, force_search: bool = False):
    """
    Gemini 호출의 단일 진입점.
    - force_search=True 이거나 prompt에 검색 트리거가 있으면 검색 도구를 활성화.
    - 429(ResourceExhausted) 발생 시 지수 백오프로 재시도. 서버 권장 retry_delay 우선.
    - 반환: (response_text, error_message). 성공 시 error_message는 None.
    """
    use_search = force_search or _should_use_search(prompt)
    model = _build_model(use_search)

    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = model.generate_content(prompt)
            return response.text, None
        except google_exceptions.ResourceExhausted as e:
            # 서버가 권장하는 대기 시간이 있으면 우선 사용, 없으면 지수 백오프 + 지터
            server_wait = getattr(getattr(e, "retry_delay", None), "seconds", 0) or 0
            wait = server_wait if server_wait > 0 else (2 ** attempt) + random.uniform(0, 1)
            if attempt < GEMINI_MAX_RETRIES - 1:
                st.warning(
                    f"⏳ API 한도 도달. {wait:.0f}초 후 재시도합니다... "
                    f"({attempt + 1}/{GEMINI_MAX_RETRIES})"
                )
                time.sleep(wait)
            else:
                return None, (
                    "🚨 Gemini 무료 등급 한도를 초과했습니다.\n\n"
                    "- 분당 한도(RPM)라면 1~2분 후 다시 시도하세요.\n"
                    "- 일일 한도(RPD)라면 태평양 표준시 자정(한국시간 오후 4~5시경) 이후 초기화됩니다.\n"
                    "- 근본 해결책: Google AI Studio에서 결제를 활성화해 Tier 1으로 전환하면 "
                    "분당·일일 한도가 크게 늘고, 입력 데이터가 학습에 사용되지 않습니다."
                )
        except Exception as e:  # noqa: BLE001 - 그 외 호출 오류는 사용자에게 그대로 전달
            return None, f"AI 호출 오류: {e}"
    return None, "알 수 없는 오류로 응답을 받지 못했습니다."

# --------------------------------------------------------------------------
# [함수 그룹 A] 국토부 실거래가 API
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
    except Exception:
        return None
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
                        "년": (item.findtext("년") or item.findtext("dealYear") or "").strip(),
                        "월": (item.findtext("월") or item.findtext("dealMonth") or "").strip(),
                        "일": (item.findtext("일") or item.findtext("dealDay") or "").strip(),
                    })
                return pd.DataFrame(data_list)
    except Exception:
        return None
    return None

def fetch_applyhome_data(service_key):
    url = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
    params = {"page": 1, "perPage": 100, "serviceKey": service_key}
    try:
        # [CHANGED] verify=False 제거 — SSL 검증 비활성화는 MITM 위험. 정상 인증서 검증으로 복원.
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "data" in data:
                df = pd.DataFrame(data["data"])
                if not df.empty:
                    df_seoul = df[df['SUBSCRPT_AREA_CODE_NM'].astype(str).str.contains('서울', na=False)]
                    if df_seoul.empty:
                        return pd.DataFrame()
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
    except Exception:
        return None
    return pd.DataFrame()

# --------------------------------------------------------------------------
# [함수 그룹 B] 한국부동산원(R-ONE) 주간 지수 기반 추정 시세 산출
# --------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def fetch_reb_weekly_index(sigungu_name, weeks_back, service_key):
    url = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
    end_date = datetime.now()
    start_date = end_date - timedelta(weeks=weeks_back + 2)

    params = {
        "KEY": service_key,
        "Type": "json",
        "pIndex": 1,
        "pSize": 100,
        "STATBL_ID": "A_2024_00178",
        "DTACYCLE_CD": "WW",
        "CLS_ID": sigungu_name,
        "START_WRTTIME": start_date.strftime("%Y%m%d"),
        "END_WRTTIME": end_date.strftime("%Y%m%d"),
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("SttsApiTblData", [{}, {}])
            if len(rows) > 1 and "row" in rows[1]:
                return pd.DataFrame(rows[1]["row"])
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

def estimate_today_price(last_price_eok, last_deal_date_str, sigungu_name, service_key):
    try:
        last_deal_date = pd.to_datetime(last_deal_date_str)
    except Exception:
        return last_price_eok, 0.0

    days_gap = (datetime.now() - last_deal_date).days
    if days_gap <= 7:
        return last_price_eok, 0.0

    weeks_gap = max(1, days_gap // 7)
    df_idx = fetch_reb_weekly_index(sigungu_name, weeks_gap, service_key)
    if df_idx.empty or "DTA_VAL" not in df_idx.columns:
        return last_price_eok, None

    weekly_changes = pd.to_numeric(df_idx["DTA_VAL"], errors="coerce").dropna() / 100
    if weekly_changes.empty:
        return last_price_eok, None

    cumulative_factor = (1 + weekly_changes).prod()
    estimated = round(last_price_eok * cumulative_factor, 2)
    change_pct = round((cumulative_factor - 1) * 100, 2)
    return estimated, change_pct

def freshness_label(deal_date_str):
    try:
        days = (datetime.now() - pd.to_datetime(deal_date_str)).days
    except Exception:
        return "❓ 미확인"
    if days <= 7:
        return "🟢 실시간급(1주)"
    if days <= 30:
        return "🟡 최신(1개월)"
    if days <= 90:
        return "🟠 보통(3개월)"
    return "🔴 참고용(3개월+)"

# --------------------------------------------------------------------------
# [함수 그룹 C] AI 자문 이력 저장/조회
# --------------------------------------------------------------------------
def save_advisory_log(advisory_type, target, conditions, ai_content, user_question=""):
    try:
        conn_log = st.connection("gsheets", type=GSheetsConnection)
        try:
            df_log = conn_log.read(worksheet="AI자문이력", ttl=0)
            if df_log is None or df_log.empty:
                df_log = pd.DataFrame(columns=["저장일시", "자문유형", "대상", "사용자조건", "AI분석내용", "사용자질문"])
        except Exception:
            df_log = pd.DataFrame(columns=["저장일시", "자문유형", "대상", "사용자조건", "AI분석내용", "사용자질문"])

        new_row = pd.DataFrame([{
            "저장일시": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "자문유형": advisory_type,
            "대상": str(target)[:200],
            "사용자조건": str(conditions)[:500],
            "AI분석내용": str(ai_content)[:5000],
            "사용자질문": str(user_question)[:500],
        }])

        df_updated = pd.concat([df_log, new_row], ignore_index=True)
        conn_log.update(worksheet="AI자문이력", data=df_updated)
        return True, len(df_updated)
    except Exception as e:
        return False, str(e)

def export_chat_to_markdown(messages, title="AI 자문 기록"):
    md = f"# {title}\n\n"
    md += f"**저장일시**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n---\n\n"
    for m in messages:
        role = "🤵 사용자" if m['role'] == 'user' else "🤖 AI 컨설턴트"
        md += f"## {role}\n\n{m['content']}\n\n---\n\n"
    return md

# --------------------------------------------------------------------------
# [2] 사이드바
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("💰 내 재정 및 청약 조건")

    with st.expander("💸 자산 및 소득 (클릭)", expanded=True):
        user_cash = st.number_input("가용 현금 (억 원)", min_value=0.0, value=3.0, step=0.1)
        user_income = st.number_input("연 소득 (천만 원)", min_value=0.0, value=7.5, step=0.5)
        target_loan_rate = st.slider("예상 대출 금리 (%)", 2.0, 8.0, 4.0)

    with st.expander("📝 청약 가점 및 특별공급 조건 (클릭)", expanded=True):
        is_homeless = st.checkbox("무주택자", value=True)
        homeless_years = st.number_input("무주택 기간 (년)", 0, 30, 5)
        is_newlywed = st.checkbox("신혼부부 (혼인기간 7년 이내)", value=False)
        is_first_time = st.checkbox("생애최초 (세대원 전원 주택소유 이력 없음)", value=False)
        children_count = st.number_input("미성년 자녀 수 (명)", 0, 10, 0)
        sub_account_years = st.number_input("청약통장 가입기간 (년)", 0, 30, 5)

    st.divider()

    st.header("🔍 실거래가 자동 수집 (배치 모드)")
    st.caption("⚠️ 한 번에 5개 구 이하 선택을 권장합니다. 다수 선택 시 정부 API 트래픽 제한으로 실패 가능성이 높아집니다.")

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

    district_groups = {
        "🏙️ 서울 강남권": ["서울 강남구", "서울 서초구", "서울 송파구", "서울 강동구"],
        "🏛️ 서울 도심권": ["서울 종로구", "서울 중구", "서울 용산구", "서울 성동구", "서울 광진구"],
        "🌳 서울 동북권": ["서울 강북구", "서울 노원구", "서울 도봉구", "서울 동대문구", "서울 성북구", "서울 중랑구"],
        "🌉 서울 서남권": ["서울 강서구", "서울 관악구", "서울 구로구", "서울 금천구", "서울 동작구", "서울 영등포구", "서울 양천구"],
        "🌲 서울 서북권": ["서울 마포구", "서울 서대문구", "서울 은평구"],
        "🏞️ 경기 1기 신도시": ["경기 성남 분당", "경기 고양 일산동", "경기 고양 일산서", "경기 안양 동안", "경기 부천시", "경기 군포시"],
        "🏗️ 경기 2기/3기": ["경기 과천시", "경기 광명시", "경기 하남시", "경기 화성시", "경기 김포시", "경기 남양주시"],
        "🌆 경기 기타": ["경기 성남 수정", "경기 성남 중원", "경기 안양 만안", "경기 수원 영통", "경기 수원 팔달",
                       "경기 용인 수지", "경기 용인 기흥", "경기 고양 덕양", "경기 구리시", "경기 의왕시"],
    }

    if 'selected_districts' not in st.session_state:
        st.session_state['selected_districts'] = []
    if 'failed_districts' not in st.session_state:
        st.session_state['failed_districts'] = []

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("✅ 모두 해제", use_container_width=True):
            st.session_state['selected_districts'] = []
            st.rerun()
    with col_btn2:
        if st.button("🎯 강남4구만", use_container_width=True):
            st.session_state['selected_districts'] = ["서울 강남구", "서울 서초구", "서울 송파구", "서울 강동구"]
            st.rerun()

    selected = []
    for group_name, districts in district_groups.items():
        with st.expander(group_name, expanded=False):
            for d in districts:
                checked = d in st.session_state['selected_districts']
                if st.checkbox(d, value=checked, key=f"chk_{d}"):
                    selected.append(d)

    st.session_state['selected_districts'] = selected
    sel_count = len(selected)

    if sel_count == 0:
        st.warning("⚠️ 선택된 구가 없습니다.")
    elif sel_count <= 5:
        st.success(f"✅ {sel_count}개 구 선택됨 (권장 범위)")
    elif sel_count <= 10:
        st.warning(f"⚠️ {sel_count}개 구 선택됨 (실패 가능성 있음)")
    else:
        st.error(f"🚨 {sel_count}개 구 선택됨 (강력히 비권장)")

    with st.expander("⚙️ 고급 호출 설정", expanded=False):
        call_interval = st.slider("호출 간격(초)", 0.1, 2.0, 0.5, 0.1)
        max_retries = st.slider("실패 시 자동 재시도 횟수", 0, 3, 2)
        months_to_fetch = st.slider("조회 월 범위", 1, 6, 2)

    apply_estimation = st.checkbox("🌟 추정 현재시세 자동 산출", value=True)
    rent_recent_only = st.checkbox("📅 전월세 최근 30일 데이터만 사용", value=False)

    fetch_clicked = st.button(f"📥 선택된 {sel_count}개 구 데이터 수집", type="primary", disabled=(sel_count == 0), use_container_width=True)

    if st.session_state['failed_districts']:
        st.divider()
        st.error(f"❌ 이전 시도에서 {len(st.session_state['failed_districts'])}개 구 실패")
        with st.expander("실패 목록 보기", expanded=True):
            for fd in st.session_state['failed_districts']:
                st.write(f"- {fd}")
        if st.button("🔄 실패한 구만 재시도", use_container_width=True):
            st.session_state['selected_districts'] = st.session_state['failed_districts'].copy()
            st.session_state['failed_districts'] = []
            st.rerun()

    if fetch_clicked and sel_count > 0:
        target_districts = {d: district_code[d] for d in selected if d in district_code}
        progress_bar = st.progress(0, text="정부 서버 연결 중...")
        status_box = st.empty()
        df_trade_list, df_rent_list, failed_list = [], [], []

        now = datetime.now()
        months = []
        cursor = now
        for _ in range(months_to_fetch):
            months.append(cursor.strftime("%Y%m"))
            cursor = (cursor.replace(day=1) - timedelta(days=1))

        total_steps = len(target_districts) * len(months) * 2
        step, success_streak = 0, 0
        current_interval = call_interval

        for name, code in target_districts.items():
            district_success = True
            district_records = 0

            for ym in months:
                step += 1
                trade_ok = False
                for attempt in range(max_retries + 1):
                    progress_bar.progress(step / total_steps, text=f"[{name}] {ym} 매매 수신 중... (시도 {attempt+1}/{max_retries+1})")
                    df_raw_trade = fetch_trade_data(code, ym, api_key_decoded)
                    if df_raw_trade is not None:
                        if not df_raw_trade.empty:
                            df_raw_trade['구'] = name
                            df_trade_list.append(df_raw_trade)
                            district_records += len(df_raw_trade)
                        trade_ok = True
                        break
                    time.sleep(current_interval * (attempt + 2))
                if not trade_ok: district_success = False
                time.sleep(current_interval)

                step += 1
                rent_ok = False
                for attempt in range(max_retries + 1):
                    progress_bar.progress(step / total_steps, text=f"[{name}] {ym} 전월세 수신 중... (시도 {attempt+1}/{max_retries+1})")
                    df_raw_rent = fetch_rent_data(code, ym, api_key_decoded)
                    if df_raw_rent is not None:
                        if not df_raw_rent.empty:
                            df_raw_rent['구'] = name
                            df_rent_list.append(df_raw_rent)
                        rent_ok = True
                        break
                    time.sleep(current_interval * (attempt + 2))
                if not rent_ok: district_success = False
                time.sleep(current_interval)

            if district_success:
                success_streak += 1
                status_box.info(f"✅ {name} 완료 ({district_records}건). 누적 성공: {success_streak}")
                if success_streak >= 5 and current_interval > 0.2:
                    current_interval = max(0.2, current_interval * 0.8)
            else:
                failed_list.append(name)
                success_streak = 0
                current_interval = min(2.0, current_interval * 1.5)
                status_box.warning(f"⚠️ {name} 실패. 간격 {current_interval:.1f}초로 조정")

        progress_bar.empty()
        status_box.empty()
        st.session_state['failed_districts'] = failed_list

        if df_trade_list:
            df_all_trade = pd.concat(df_trade_list, ignore_index=True)
            df_clean = pd.DataFrame()
            df_clean['아파트명'] = df_all_trade['아파트']
            df_clean['지역'] = df_all_trade['구'] + " " + df_all_trade['법정동']
            df_clean['시군구'] = df_all_trade['구']
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

                if rent_recent_only:
                    df_all_rent['년'] = df_all_rent['년'].astype(str).str.zfill(4)
                    df_all_rent['월'] = df_all_rent['월'].astype(str).str.zfill(2)
                    df_all_rent['일'] = df_all_rent['일'].astype(str).str.zfill(2)
                    df_all_rent['신고일'] = pd.to_datetime(df_all_rent['년'] + "-" + df_all_rent['월'] + "-" + df_all_rent['일'], errors='coerce')
                    cutoff = datetime.now() - timedelta(days=30)
                    df_all_rent = df_all_rent[df_all_rent['신고일'] >= cutoff]

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

            df_clean['데이터신선도'] = df_clean['거래일'].apply(freshness_label)

            if apply_estimation:
                with st.spinner("🌟 한국부동산원 지수 기반 추정 시세 계산 중..."):
                    est_prices, cum_changes = [], []
                    for _, row in df_clean.iterrows():
                        est, chg = estimate_today_price(row['매매가(억)'], row['거래일'], row['시군구'], reb_api_key)
                        est_prices.append(est)
                        cum_changes.append(chg if chg is not None else 0.0)
                    df_clean['추정현재시세(억)'] = est_prices
                    df_clean['누적변동률(%)'] = cum_changes
            else:
                df_clean['추정현재시세(억)'] = df_clean['매매가(억)']
                df_clean['누적변동률(%)'] = 0.0

            df_clean['전고점(억)'] = 0.0
            df_clean['입지점수'] = 0
            df_clean = df_clean.sort_values(by='거래일', ascending=False)

            cols_to_keep = [
                '아파트명', '지역', '평형', '층', '건축년도',
                '매매가(억)', '추정현재시세(억)', '누적변동률(%)', '데이터신선도',
                '전세가(억)', '월세보증금(억)', '월세액(만원)',
                '거래일', '전고점(억)', '입지점수'
            ]
            st.session_state['fetched_data'] = df_clean[cols_to_keep]
            st.success(f"✅ 수집 완료! 총 {len(df_clean)}건")
        else:
            st.error("⚠️ 수집된 데이터가 없습니다.")

# --------------------------------------------------------------------------
# [3] 메인 화면 (4개 탭)
# --------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "📥 실거래가 저장",
    "🏆 랭킹 & 💬 매매 자문",
    "📅 서울 청약 & 💬 청약 자문",
    "📚 자문 이력 조회"
])

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception:
    pass

# --- TAB 1: 데이터 저장 ---
with tab1:
    st.subheader("📡 실시간 실거래 시세 + 추정 현재시세")
    if 'fetched_data' in st.session_state:
        df_new = st.session_state['fetched_data']
        search_apt = st.text_input("아파트 검색", placeholder="예: 래미안")
        df_display = df_new[df_new['아파트명'].astype(str).str.contains(search_apt)] if search_apt else df_new
        st.dataframe(df_display.style.format({
            '매매가(억)': '{:.2f}', '추정현재시세(억)': '{:.2f}', '누적변동률(%)': '{:+.2f}%',
            '전세가(억)': '{:.2f}', '월세보증금(억)': '{:.2f}'
        }), use_container_width=True)

        if st.button("💾 구글 시트에 저장 (기준정보 반영)"):
            try:
                try:
                    df_master = conn.read(worksheet="기준정보", ttl=0)
                    master_dict = {str(row['아파트명']).replace(" ", "").strip(): {'전고점': row.get('전고점(억)', 0), '점수': row.get('입지점수', 0)} for _, row in df_master.iterrows()}
                except Exception: master_dict = {}

                for idx, row in df_new.iterrows():
                    name = str(row['아파트명']).replace(" ", "").strip()
                    if name in master_dict:
                        df_new.at[idx, '전고점(억)'] = master_dict[name]['전고점']
                        df_new.at[idx, '입지점수'] = master_dict[name]['점수']

                try: df_current = conn.read(ttl=0)
                except Exception: df_current = pd.DataFrame()

                cols = ['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '추정현재시세(억)', '누적변동률(%)', '데이터신선도', '전세가(억)', '월세보증금(억)', '월세액(만원)', '거래일', '전고점(억)', '입지점수']

                if not df_current.empty:
                    for c in cols:
                        if c not in df_current.columns:
                            df_current[c] = "-" if c in ['층', '건축년도', '거래일', '데이터신선도'] else 0

                if df_current.empty: final_df = df_new[cols].copy()
                else:
                    current_dict = {f"{str(r['아파트명']).replace(' ', '').strip()}_{r['평형']}": r.to_dict() for _, r in df_current.iterrows()}
                    for _, row in df_new.iterrows():
                        key = f"{str(row['아파트명']).replace(' ', '').strip()}_{row['평형']}"
                        if key in current_dict:
                            current_dict[key].update({
                                '매매가(억)': row['매매가(억)'], '추정현재시세(억)': row['추정현재시세(억)'], '누적변동률(%)': row['누적변동률(%)'], '데이터신선도': row['데이터신선도'],
                                '층': row['층'], '건축년도': row['건축년도'], '전세가(억)': row['전세가(억)'], '월세보증금(억)': row['월세보증금(억)'], '월세액(만원)': row['월세액(만원)'], '거래일': row['거래일']
                            })
                            if row['전고점(억)'] > 0: current_dict[key]['전고점(억)'] = row['전고점(억)']
                            if row['입지점수'] > 0: current_dict[key]['입지점수'] = row['입지점수']
                        else: current_dict[key] = row[cols].to_dict()
                    final_df = pd.DataFrame(list(current_dict.values()))[cols]

                conn.update(data=final_df)
                st.balloons()
                st.success("✅ 저장 완료!")
                time.sleep(1)
                st.rerun()
            except Exception as e: st.error(f"저장 실패: {e}")
    else: st.info("👈 왼쪽 사이드바에서 [실거래가 가져오기] 버튼을 눌러주세요.")

# --- TAB 2: 매매 분석 (랭킹 + AI 대화) ---
with tab2:
    try:
        df_sheet = conn.read(ttl=0)
        if not df_sheet.empty and '매매가(억)' in df_sheet.columns:
            for c in ['층', '건축년도', '추정현재시세(억)', '누적변동률(%)', '데이터신선도']:
                if c not in df_sheet.columns: df_sheet[c] = "-" if c in ['층', '건축년도', '데이터신선도'] else 0

            df_sheet['추정현재시세(억)'] = pd.to_numeric(df_sheet['추정현재시세(억)'], errors='coerce').fillna(0)
            df_sheet.loc[df_sheet['추정현재시세(억)'] == 0, '추정현재시세(억)'] = df_sheet['매매가(억)']

            st.header("🏆 AI 추천 랭킹 (추정 현재시세 기준)")
            df_rank = df_sheet.copy()
            df_rank['하락률(%)'] = df_rank.apply(lambda x: ((x['전고점(억)'] - x['추정현재시세(억)']) / x['전고점(억)'] * 100) if x.get('전고점(억)', 0) > 0 else 0, axis=1)
            df_rank['갭(억)'] = df_rank['추정현재시세(억)'] - df_rank['전세가(억)']

            with st.expander("🕵️‍♂️ 조건 설정 (필터 펼치기)", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    pyung_range = st.slider("원하는 평수", 10, 80, (20, 40), step=1)
                    exclude_small = st.checkbox("20평 미만 제외", value=True)
                with c2: price_max = st.slider("최대 매매가 (억, 추정시세 기준)", 5, 50, 20)
                with c3: gap_max = st.slider("최대 갭 투자금 (억)", 1, 20, 10)

            df_filtered = df_rank[(df_rank['평형'] >= pyung_range[0]) & (df_rank['평형'] <= pyung_range[1])]
            if exclude_small: df_filtered = df_filtered[df_filtered['평형'] >= 20]
            df_filtered = df_filtered[df_filtered['추정현재시세(억)'] <= price_max]
            df_invest_filtered = df_filtered[df_filtered['갭(억)'] <= gap_max]

            regions = ["전체"] + sorted(df_filtered['지역'].astype(str).unique().tolist())
            selected_region_rank = st.selectbox("지역별 필터", regions)
            if selected_region_rank != "전체":
                df_filtered = df_filtered[df_filtered['지역'] == selected_region_rank]
                df_invest_filtered = df_invest_filtered[df_invest_filtered['지역'] == selected_region_rank]

            col_r1, col_r2 = st.columns(2)
            with col_r1:
                st.subheader(f"🏡 실거주 추천 ({len(df_filtered)}건)")
                if not df_filtered.empty:
                    st.dataframe(df_filtered.sort_values(by=['하락률(%)', '입지점수'], ascending=[False, False])[['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '추정현재시세(억)', '데이터신선도', '하락률(%)']].style.format({'매매가(억)': '{:.1f}', '추정현재시세(억)': '{:.1f}', '하락률(%)': '{:.1f}%'}), height=500, use_container_width=True)
                else: st.info("조건에 맞는 매물이 없습니다.")
            with col_r2:
                st.subheader(f"💰 갭투자 추천 ({len(df_invest_filtered)}건)")
                if not df_invest_filtered.empty:
                    st.dataframe(df_invest_filtered.sort_values(by=['갭(억)', '입지점수'], ascending=[True, False])[['아파트명', '지역', '평형', '층', '건축년도', '추정현재시세(억)', '전세가(억)', '갭(억)', '데이터신선도']].style.format({'추정현재시세(억)': '{:.1f}', '전세가(억)': '{:.1f}', '갭(억)': '{:.1f}'}), height=500, use_container_width=True)
                else: st.info("조건에 맞는 매물이 없습니다.")

            st.divider()

            # --- 단건 심층 자문 ---
            st.header("💬 AI 매매/갭투자 자문 (실시간 검색 탑재)")
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
                c2.metric("추정 현재시세", f"{target['추정현재시세(억)']:.2f}억", f"직전 실거래 {target['매매가(억)']:.2f}억")
                c3.metric("실제 전세가", f"{target['전세가(억)']:.2f}억")
                c4.metric("데이터 신선도", target.get('데이터신선도', '❓ 미확인'))

                if st.button("🚀 매매 심층 분석 시작", type="primary"):
                    loan_needed = target['추정현재시세(억)'] - user_cash
                    dsr_rough = (loan_needed * (target_loan_rate / 100)) / (user_income / 10) * 100 if user_income > 0 else 0
                    system_prompt = f"""
                    너는 최고의 부동산 투자 전문가야. 아래 팩트(국토부 실거래가 + 한국부동산원 지수 보정)를 바탕으로 사용자와 대화해줘.
                    [매물] {target['아파트명']} ({target['지역']}), {target.get('건축년도','-')}년 건축, {target.get('층','-')}층, {target['평형']}평
                    [가격 정보 — 중요]
                    - 직전 실거래가: {target['매매가(억)']}억 (거래일: {target.get('거래일', '-')})
                    - 추정 현재시세: {target['추정현재시세(억)']:.2f}억 (R-ONE 지수 누적 {target.get('누적변동률(%)', 0):+.2f}% 적용)
                    - 최근 평균 전세가: {target['전세가(억)']:.2f}억, 전고점: {target.get('전고점(억)', 0)}억
                    [재정] 현금 {user_cash}억, 연소득 {user_income}천만, 금리 {target_loan_rate}%, 예상 DSR {dsr_rough:.1f}%

                    🔥중요 지시사항🔥
                    사용자가 이 데이터베이스에 없는 다른 아파트와의 '비교'를 요청하거나, 실시간 호재/시세/학군 등을 물어보면 반드시 너에게 내장된 '구글 실시간 검색(Google Search)' 기능을 활발하게 사용해서 네이버 부동산 시세나 최신 뉴스를 찾아내서 대답해줘.

                    먼저 이 매물의 가격 적정성, 층/연식을 고려한 적합성, 자금 여력을 종합 분석해줘.
                    """
                    st.session_state['context_prompt_tab2'] = system_prompt
                    with st.spinner("AI가 입체적으로 분석 중입니다..."):
                        # [CHANGED] 통합 헬퍼 사용. 초기 심층 분석은 검색을 강제 활성화(force_search=True).
                        text, err = ask_gemini(system_prompt, force_search=True)
                        if err:
                            st.error(err)
                        else:
                            st.session_state['messages_tab2'].append({"role": "assistant", "content": text})
                            st.rerun()

                if st.session_state.get('messages_tab2'):
                    save_col1, save_col2 = st.columns(2)
                    with save_col1:
                        if st.button("💾 이 자문을 시트에 저장", key="save_tab2", use_container_width=True):
                            full_content = "\n\n---\n\n".join([f"[{m['role']}]\n{m['content']}" for m in st.session_state['messages_tab2']])
                            conditions_str = f"매물: {target['아파트명']}({target['평형']}평) | 현금: {user_cash}억 | 연소득: {user_income}천만"
                            ok, info = save_advisory_log(advisory_type="매물단건", target=selected_key, conditions=conditions_str, ai_content=full_content)
                            if ok: st.success(f"✅ 시트에 저장 완료 (누적 {info}건)")
                            else: st.error(f"저장 실패: {info}")
                    with save_col2:
                        md_text = export_chat_to_markdown(st.session_state['messages_tab2'], title=f"매물 자문 - {selected_key}")
                        st.download_button("📥 마크다운 다운로드", data=md_text, file_name=f"자문_{datetime.now().strftime('%Y%m%d_%H%M')}.md", mime="text/markdown", key="dl_tab2", use_container_width=True)

                for msg in st.session_state.get('messages_tab2', []):
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input("질문: 주변 단지와 비교해줘! 학군은 어때? 등 자유롭게 물어보세요."):
                    with st.chat_message("user"): st.markdown(prompt)
                    st.session_state['messages_tab2'].append({"role": "user", "content": prompt})
                    with st.chat_message("assistant"):
                        message_placeholder = st.empty()
                        # [CHANGED] 이력 윈도잉(_build_followup_prompt) + 조건부 검색 + 재시도를 헬퍼에 위임.
                        # 전체 이력을 재구성하지 않으므로 토큰(TPM) 소모가 크게 줄어든다.
                        ctx = st.session_state.get('context_prompt_tab2', '')
                        if ctx:
                            final_prompt = _build_followup_prompt(ctx, st.session_state['messages_tab2'], prompt)
                        else:
                            final_prompt = prompt
                        text, err = ask_gemini(final_prompt)
                        if err:
                            message_placeholder.error(err)
                        else:
                            message_placeholder.markdown(text)
                            st.session_state['messages_tab2'].append({"role": "assistant", "content": text})
            else: st.info("👆 분석할 매물을 선택해주세요.")

            st.divider()

            # --- 지역 기반 추천 자문 ---
            st.header("🎯 지역 기반 AI 단지 추천 (실시간 검색 탑재)")
            rec_col1, rec_col2 = st.columns(2)
            with rec_col1:
                df_sheet['_시군구'] = df_sheet['지역'].astype(str).str.split(' ').str[:2].str.join(' ')
                region_options = sorted(df_sheet['_시군구'].unique().tolist())
                selected_rec_region = st.selectbox("📍 추천받을 지역", region_options, index=None, placeholder="예: 서울 강남구")
            with rec_col2:
                rec_budget_max = st.number_input("💰 최대 예산 (억)", min_value=1.0, value=9.0, step=1.0)

            rec_purposes = st.multiselect("🎯 투자 목적 (다중 선택 가능)", ["실거주 (장기보유)", "시세차익 투자", "갭투자 (전세 레버리지)", "월세 수익형"], default=["실거주 (장기보유)"])
            weights = {}
            if len(rec_purposes) > 1:
                with st.expander("⚖️ 목적별 가중치 조정", expanded=True):
                    raw_weights = {}
                    cols = st.columns(len(rec_purposes))
                    default_w = round(100 / len(rec_purposes))
                    for i, p in enumerate(rec_purposes):
                        with cols[i]: raw_weights[p] = st.slider(p, 0, 100, default_w, 5, key=f"w_{p}")
                    total_w = sum(raw_weights.values()) or 1
                    weights = {p: w / total_w for p, w in raw_weights.items()}
            else:
                for p in rec_purposes: weights[p] = 1.0

            rec_col4, rec_col5 = st.columns(2)
            with rec_col4: rec_pyung_range = st.slider("📐 평형 범위", 10, 80, (20, 30), key="rec_pyung")
            with rec_col5: rec_top_n = st.slider("🏆 추천 단지 수", 3, 15, 10)

            if 'messages_recommend' not in st.session_state: st.session_state['messages_recommend'] = []
            if 'context_recommend' not in st.session_state: st.session_state['context_recommend'] = ""

            if st.button("🚀 AI 추천 분석 시작", type="primary", key="btn_recommend"):
                if selected_rec_region is None: st.error("⚠️ 지역을 선택해 주세요.")
                elif not rec_purposes: st.error("⚠️ 최소 1개의 투자 목적을 선택해 주세요.")
                else:
                    df_candidates = df_sheet[(df_sheet['_시군구'] == selected_rec_region) & (df_sheet['평형'] >= rec_pyung_range[0]) & (df_sheet['평형'] <= rec_pyung_range[1]) & (df_sheet['추정현재시세(억)'] <= rec_budget_max) & (df_sheet['추정현재시세(억)'] > 0)].copy()
                    if df_candidates.empty: st.warning("⚠️ 조건에 맞는 단지가 없습니다.")
                    else:
                        df_candidates['갭(억)'] = df_candidates['추정현재시세(억)'] - df_candidates['전세가(억)']
                        def normalize(series, ascending=False):
                            if series.empty or series.max() == series.min(): return pd.Series([50] * len(series), index=series.index)
                            normalized = (series - series.min()) / (series.max() - series.min()) * 100
                            return (100 - normalized) if ascending else normalized

                        if "실거주 (장기보유)" in rec_purposes:
                            age_score = normalize(2026 - pd.to_numeric(df_candidates['건축년도'], errors='coerce').fillna(1990), ascending=True)
                            df_candidates['_점수_실거주'] = (normalize(df_candidates['입지점수']) * 0.6 + age_score * 0.4)
                        else: df_candidates['_점수_실거주'] = 0

                        if "시세차익 투자" in rec_purposes:
                            df_candidates['하락률(%)'] = df_candidates.apply(lambda x: ((x['전고점(억)'] - x['추정현재시세(억)']) / x['전고점(억)'] * 100) if x.get('전고점(억)', 0) > 0 else 0, axis=1)
                            df_candidates['_점수_시세차익'] = (normalize(df_candidates['하락률(%)']) * 0.7 + normalize(df_candidates['입지점수']) * 0.3)
                        else: df_candidates['_점수_시세차익'] = 0

                        if "갭투자 (전세 레버리지)" in rec_purposes:
                            df_gap = df_candidates[df_candidates['갭(억)'] > 0]
                            df_candidates['_점수_갭투자'] = 0.0
                            if not df_gap.empty: df_candidates.loc[df_gap.index, '_점수_갭투자'] = (normalize(df_gap['갭(억)'], ascending=True) * 0.7 + normalize(df_gap['입지점수']) * 0.3)
                        else: df_candidates['_점수_갭투자'] = 0

                        if "월세 수익형" in rec_purposes:
                            df_candidates['연수익률(%)'] = df_candidates.apply(lambda x: (x['월세액(만원)'] * 12 / 10000) / x['추정현재시세(억)'] * 100 if x['추정현재시세(억)'] > 0 and x['월세액(만원)'] > 0 else 0, axis=1)
                            df_candidates['_점수_월세'] = normalize(df_candidates['연수익률(%)'])
                        else: df_candidates['_점수_월세'] = 0

                        score_map = {"실거주 (장기보유)": '_점수_실거주', "시세차익 투자": '_점수_시세차익', "갭투자 (전세 레버리지)": '_점수_갭투자', "월세 수익형": '_점수_월세'}
                        df_candidates['종합점수'] = sum(df_candidates[score_map[p]] * w for p, w in weights.items())
                        df_top = df_candidates.sort_values(by='종합점수', ascending=False).head(rec_top_n)

                        if df_top.empty: st.warning("⚠️ 추천 단지가 없습니다.")
                        else:
                            display_cols = [c for c in ['아파트명', '지역', '평형', '층', '건축년도', '매매가(억)', '추정현재시세(억)', '전세가(억)', '갭(억)', '종합점수', '데이터신선도', '거래일'] if c in df_top.columns]
                            st.subheader(f"📊 1차 후보 단지 ({len(df_top)}건)")
                            st.dataframe(df_top[display_cols].style.format({'매매가(억)': '{:.2f}', '추정현재시세(억)': '{:.2f}', '전세가(억)': '{:.2f}', '갭(억)': '{:.2f}', '종합점수': '{:.1f}점'}), use_container_width=True, hide_index=True)

                            system_prompt_rec = f"""
                            너는 대한민국 최고의 부동산 컨설턴트야. 아래 리스트는 사용자가 수집한 실거래가 기반 데이터야.
                            [요청] 지역: {selected_rec_region}, 예산: {rec_budget_max}억 이하, 평형: {rec_pyung_range[0]}~{rec_pyung_range[1]}평
                            [재정] 현금: {user_cash}억, 연소득: {user_income}천만원
                            [후보 리스트] {df_top[display_cols].to_string(index=False)}

                            🔥중요 지시사항🔥
                            사용자가 위 리스트에 없는 다른 단지와의 '비교'를 요청하거나, 실시간 정보를 물어보면 반드시 너에게 내장된 '구글 실시간 검색(Google Search)' 기능을 적극적으로 활용해서 외부 데이터도 함께 비교해줘.

                            리스트에 있는 단지 중 BEST 1~2곳을 뽑고 각 단지의 장단점과 자금 조달 시나리오를 구체적으로 짜줘.
                            """
                            st.session_state['context_recommend'] = system_prompt_rec
                            st.session_state['messages_recommend'] = []
                            with st.spinner("AI가 다중 목적 종합 분석 중입니다..."):
                                # [CHANGED] 통합 헬퍼 사용 + 초기 분석은 검색 강제 활성화.
                                text, err = ask_gemini(system_prompt_rec, force_search=True)
                                if err:
                                    st.error(err)
                                else:
                                    st.session_state['messages_recommend'].append({"role": "assistant", "content": text})
                                    st.rerun()

            if st.session_state.get('messages_recommend'):
                st.divider()
                st.subheader("💬 AI 추천 분석 결과 및 후속 상담")
                rec_save_col1, rec_save_col2 = st.columns(2)
                with rec_save_col1:
                    if st.button("💾 이 추천 분석을 시트에 저장", key="save_recommend", use_container_width=True):
                        full_content = "\n\n---\n\n".join([f"[{m['role']}]\n{m['content']}" for m in st.session_state['messages_recommend']])
                        conditions_str = f"지역: {selected_rec_region} | 예산: {rec_budget_max}억"
                        ok, info = save_advisory_log(advisory_type="지역추천", target=selected_rec_region, conditions=conditions_str, ai_content=full_content)
                        if ok: st.success("✅ 시트에 저장 완료")
                        else: st.error("저장 실패")
                with rec_save_col2:
                    md_text = export_chat_to_markdown(st.session_state['messages_recommend'], title=f"지역 추천 자문 - {selected_rec_region}")
                    st.download_button("📥 마크다운 다운로드", data=md_text, file_name=f"추천_{datetime.now().strftime('%Y%m%d_%H%M')}.md", mime="text/markdown", key="dl_recommend", use_container_width=True)

                for msg in st.session_state['messages_recommend']:
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if rec_prompt := st.chat_input("추천 결과에 대한 외부 단지 비교, 검색 등 후속 질문", key="chat_recommend"):
                    with st.chat_message("user"): st.markdown(rec_prompt)
                    st.session_state['messages_recommend'].append({"role": "user", "content": rec_prompt})
                    with st.chat_message("assistant"):
                        msg_placeholder = st.empty()
                        # [CHANGED] 이력 윈도잉 + 조건부 검색 + 재시도를 헬퍼에 위임.
                        final_prompt = _build_followup_prompt(
                            st.session_state['context_recommend'],
                            st.session_state['messages_recommend'],
                            rec_prompt,
                        )
                        text, err = ask_gemini(final_prompt)
                        if err:
                            msg_placeholder.error(err)
                        else:
                            msg_placeholder.markdown(text)
                            st.session_state['messages_recommend'].append({"role": "assistant", "content": text})

    except Exception as e: st.error(f"오류: {e}")

# --- TAB 3: 서울 청약 일정 및 자문 ---
with tab3:
    st.header("📅 서울 아파트 청약 추천 및 컨설팅 (실시간 검색 탑재)")
    st.info("💡 실시간 청약 공고 + 웹 검색을 통해 정확한 분양가와 주변 시세를 찾아 분석해 드립니다.")

    if st.button("🔄 최신 서울 청약 일정 불러오기", type="primary"):
        with st.spinner("청약홈 서버에서 서울 지역 공고를 가져오는 중입니다..."):
            df_apply = fetch_applyhome_data(api_key_decoded)
            st.session_state['apply_data'] = df_apply
            st.session_state['messages_tab3'] = []

    if 'apply_data' in st.session_state:
        df_apply = st.session_state['apply_data']
        if df_apply is not None and not df_apply.empty:
            st.success(f"✅ 총 {len(df_apply)}건의 진행/예정 중인 서울 청약 공고를 찾았습니다!")
            st.dataframe(df_apply, use_container_width=True, hide_index=True)
            st.divider()

            st.subheader("🤖 AI 청약 맞춤형 분석 및 전략 추천")
            if 'messages_tab3' not in st.session_state: st.session_state['messages_tab3'] = []

            if st.button("✨ 내 조건에 맞는 단지 추천 및 자문 시작", type="primary"):
                apply_summary = df_apply[['아파트명(청약단지)', '지역(공급위치)', '공급규모(세대)', '청약시작일']].to_string(index=False)
                homeless_str = f"무주택 {homeless_years}년" if is_homeless else "유주택자"
                newlywed_str = "신혼부부(O)" if is_newlywed else "신혼부부(X)"
                first_time_str = "생애최초(O)" if is_first_time else "생애최초(X)"

                system_prompt_tab3 = f"""
                너는 최고의 부동산 청약 및 특별공급 전문가야.
                [현재 청약 공고 리스트]
                {apply_summary}
                [사용자 스펙]
                - 자본: 현금 {user_cash}억 원 / 연소득 {user_income}천만 원 / 대출금리 {target_loan_rate}%
                - 특공조건: {homeless_str}, {newlywed_str}, {first_time_str}, 미성년 자녀 수 {children_count}명
                - 청약통장: 가입기간 {sub_account_years}년

                🔥중요 지시사항🔥
                이 API 공고에는 분양가가 없습니다. 반드시 네게 내장된 '구글 실시간 검색(Google Search)'을 통해
                위 리스트에 있는 주요 아파트들의 최신 예상 분양가, 주변 단지 시세(안전마진 확인), 경쟁률 등을 적극 검색해서 상세한 전략을 짜줘.

                1. 맞춤형 전형 추천 (가점제/추첨제/특공)
                2. 추천 단지 BEST 2
                3. 검색한 예상 분양가 기반의 자금 조달 시나리오
                """
                st.session_state['context_prompt_tab3'] = system_prompt_tab3

                with st.spinner("AI가 청약 자격과 실시간 시세를 검색해 분석 중입니다..."):
                    # [CHANGED] 통합 헬퍼 사용 + 초기 분석은 검색 강제 활성화.
                    text, err = ask_gemini(system_prompt_tab3, force_search=True)
                    if err:
                        st.error(err)
                    else:
                        st.session_state['messages_tab3'] = [{"role": "assistant", "content": text}]
                        st.rerun()

            if st.session_state.get('messages_tab3'):
                t3_save_col1, t3_save_col2 = st.columns(2)
                with t3_save_col1:
                    if st.button("💾 이 청약 자문을 시트에 저장", key="save_tab3", use_container_width=True):
                        full_content = "\n\n---\n\n".join([f"[{m['role']}]\n{m['content']}" for m in st.session_state['messages_tab3']])
                        ok, info = save_advisory_log(advisory_type="청약", target=f"서울 청약 ({datetime.now().strftime('%Y-%m')})", conditions=f"자금 {user_cash}억", ai_content=full_content)
                        if ok: st.success("✅ 시트에 저장 완료")
                with t3_save_col2:
                    md_text = export_chat_to_markdown(st.session_state['messages_tab3'], title="서울 청약 자문")
                    st.download_button("📥 마크다운 다운로드", data=md_text, file_name=f"청약자문_{datetime.now().strftime('%Y%m%d_%H%M')}.md", mime="text/markdown", key="dl_tab3", use_container_width=True)

            for msg in st.session_state.get('messages_tab3', []):
                with st.chat_message(msg['role']): st.markdown(msg['content'])

            if prompt := st.chat_input("청약 단지의 분양가, 주변 다른 단지와의 비교를 자유롭게 질문하세요!"):
                with st.chat_message("user"): st.markdown(prompt)
                st.session_state['messages_tab3'].append({"role": "user", "content": prompt})

                with st.chat_message("assistant"):
                    message_placeholder = st.empty()
                    # [CHANGED] 이력 윈도잉 + 조건부 검색 + 재시도를 헬퍼에 위임.
                    ctx = st.session_state.get('context_prompt_tab3', '')
                    if ctx:
                        final_prompt = _build_followup_prompt(ctx, st.session_state['messages_tab3'], prompt)
                    else:
                        final_prompt = prompt
                    text, err = ask_gemini(final_prompt)
                    if err:
                        message_placeholder.error(err)
                    else:
                        message_placeholder.markdown(text)
                        st.session_state['messages_tab3'].append({"role": "assistant", "content": text})

        elif df_apply is not None and df_apply.empty: st.warning("현재 진행 중인 서울 청약 공고가 없습니다.")
        else: st.error("🚨 청약 데이터를 불러오지 못했습니다.")

# --- TAB 4: AI 자문 이력 조회 ---
with tab4:
    st.header("📚 AI 자문 이력 아카이브")
    st.info("💡 시트에 저장된 모든 AI 자문 이력을 조회하고 시계열로 분석합니다.")
    try:
        conn_view = st.connection("gsheets", type=GSheetsConnection)
        try: df_history = conn_view.read(worksheet="AI자문이력", ttl=0)
        except Exception: df_history = pd.DataFrame()

        if df_history is None or df_history.empty:
            st.warning("저장된 자문 이력이 없습니다.")
        else:
            df_history = df_history.dropna(subset=['저장일시']).sort_values(by='저장일시', ascending=False)
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1: filter_type = st.selectbox("자문 유형 필터", ["전체"] + sorted(df_history['자문유형'].dropna().unique().tolist()))
            with col_f2: search_target = st.text_input("대상 검색", placeholder="예: 강남구")
            with col_f3: show_count = st.slider("표시 건수", 5, 50, 20)

            df_view = df_history.copy()
            if filter_type != "전체": df_view = df_view[df_view['자문유형'] == filter_type]
            if search_target: df_view = df_view[df_view['대상'].astype(str).str.contains(search_target, na=False)]
            df_view = df_view.head(show_count)
            st.caption(f"📊 총 {len(df_history)}건 중 {len(df_view)}건 표시")

            for idx, row in df_view.iterrows():
                with st.expander(f"🗓️ {row['저장일시']} | [{row['자문유형']}] {row['대상']}", expanded=False):
                    st.markdown(f"**📋 분석 시점 조건**: {row['사용자조건']}")
                    st.divider()
                    st.markdown(row['AI분석내용'])
    except Exception as e: st.error(f"이력 조회 오류: {e}")
