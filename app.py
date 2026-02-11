import streamlit as st
import requests
import datetime
from urllib.parse import unquote

st.set_page_config(page_title="API 정밀 진단", layout="wide")
st.title("🚑 부동산 API 긴급 정밀 진단")

# 1. API 키 확인
if "PUBLIC_DATA_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 파일에 PUBLIC_DATA_KEY가 없습니다!")
    st.stop()

raw_key = st.secrets["PUBLIC_DATA_KEY"]
decoded_key = unquote(raw_key) # 키 디코딩 (필수)

st.write(f"🔑 **현재 입력된 키(일부):** `{raw_key[:10]}...`")

# 2. 진단 설정
st.info("서울 강남구의 가장 최근 데이터를 요청하여, 서버가 거절하는 '진짜 이유'를 밝혀냅니다.")

if st.button("🚀 진단 시작 (클릭)"):
    # 테스트 변수: 강남구, 2024년 1월 (데이터가 확실히 있는 기간)
    TEST_CODE = "11680" 
    TEST_YM = "202401"
    
    # 공공데이터포털 공식 URL (아파트 매매 실거래가 상세 자료)
    url = "http://openapi.molit.go.kr/OpenAPI_ToolInstallPackage/service/rest/RTMSOBJSvc/getRTMSDataSvcAptTradeDev"
    
    # 요청 파라미터 (일부러 라이브러리 안 쓰고 직접 보냅니다)
    params = {
        "serviceKey": decoded_key, # 디코딩된 키 사용
        "LAWD_CD": TEST_CODE,
        "DEAL_YMD": TEST_YM,
        "numOfRows": 5,
        "pageNo": 1
    }
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📡 1. 연결 시도 결과")
        try:
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                st.success(f"✅ 서버 연결 성공 (상태코드: 200)")
            else:
                st.error(f"❌ 서버 연결 실패 (상태코드: {response.status_code})")
                
        except Exception as e:
            st.error(f"❌ 연결 자체 실패: {e}")
            st.stop()

    with col2:
        st.subheader("📝 2. 서버 응답 원본")
        content = response.text
        st.code(content, language="xml")

    st.divider()
    
    # 3. 결과 자동 분석
    st.subheader("🧐 3. AI 진단 결과")
    
    if "<totalCount>0</totalCount>" in content:
        st.warning("⚠️ **진단: 데이터 없음 (0건)**")
        st.write("연결은 됐는데 데이터가 없다고 합니다. '기간'이나 '지역코드' 문제일 수 있습니다.")
        
    elif "SERVICE_KEY_IS_NOT_REGISTERED" in content:
        st.error("⛔ **진단: 인증키 미등록 오류**")
        st.write("1. 공공데이터포털에서 **'활용신청'**이 아직 승인 안 됐거나,")
        st.write("2. 신청한 API가 **'아파트매매 실거래 상세 자료'**가 아닌 엄한 것일 수 있습니다.")
        st.write("3. 혹은 **Encoding 키**를 넣으셨다면 **Decoding 키**로 바꿔보세요.")

    elif "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in content:
        st.error("⛔ **진단: 트래픽 초과**")
        st.write("오늘치 사용량(1,000건)을 다 썼습니다. (내일 0시에 풀림)")
        
    elif "OpenAPI_ServiceResponse" in content and "<resultCode>00</resultCode>" in content:
        if "<item>" in content:
            st.balloons()
            st.success("🎉 **진단: 정상! 완벽합니다.**")
            st.write("데이터가 정상적으로 들어오고 있습니다. 이전 에러는 일시적이었거나 코드 로직 문제였습니다.")
        else:
            st.warning("❓ **진단: 정상 응답이지만 내용이 비어있음**")
            st.write("키도 맞고 접속도 되는데, 해당 월에 거래가 하나도 없다고 합니다.")
            
    else:
        st.error("❓ **진단: 알 수 없는 오류**")
        st.write("오른쪽의 [서버 응답 원본]을 확인해보세요.")
