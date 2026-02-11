import streamlit as st
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from urllib.parse import unquote

st.set_page_config(page_title="API 정밀 진단 (New URL)", layout="wide")
st.title("🚑 부동산 API 긴급 진단 (최신 주소 적용)")

# 1. API 키 확인
if "PUBLIC_DATA_KEY" not in st.secrets:
    st.error("🚨 secrets.toml 파일에 PUBLIC_DATA_KEY가 없습니다!")
    st.stop()

raw_key = st.secrets["PUBLIC_DATA_KEY"]
decoded_key = unquote(raw_key) 

st.write(f"🔑 **현재 입력된 키(일부):** `{raw_key[:10]}...`")

# 2. 진단 설정
st.info("정부의 최신 서버 주소(apis.data.go.kr)로 강남구 데이터를 요청합니다.")

if st.button("🚀 진단 시작 (클릭)"):
    # 테스트 변수: 강남구, 2024년 1월
    TEST_CODE = "11680" 
    TEST_YM = "202401"
    
    # [수정됨] 최신 공공데이터포털 URL (국토교통부 아파트매매 실거래 상세 자료)
    url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    
    params = {
        "serviceKey": decoded_key, # 디코딩된 키
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
                st.success(f"✅ 서버 연결 성공 (200 OK)")
            else:
                st.error(f"❌ 서버 연결 실패 (상태코드: {response.status_code})")
                
        except Exception as e:
            st.error(f"❌ 연결 자체 실패: {e}")
            st.stop()

    with col2:
        st.subheader("📝 2. 서버 응답 내용")
        content = response.text
        # XML 내용 보여주기
        st.code(content, language="xml")

    st.divider()
    
    # 3. 결과 분석
    st.subheader("🧐 3. 최종 진단")
    
    if "<resultCode>00</resultCode>" in content:
        st.balloons()
        st.success("🎉 **키와 서버 모두 정상입니다!**")
        st.write("이제 메인 프로그램을 '최신 라이브러리 버전'으로 다시 실행하면 됩니다.")
        
    elif "SERVICE_KEY_IS_NOT_REGISTERED" in content:
        st.error("⛔ **에러: 인증키 미등록**")
        st.write("공공데이터포털에서 활용신청이 안 됐거나, 잘못된 키를 복사했습니다.")
        
    elif "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in content:
        st.error("⛔ **에러: 트래픽 초과**")
        st.write("오늘 사용량을 다 썼습니다. 내일 다시 시도하세요.")
        
    else:
        st.warning("⚠️ **알 수 없는 응답**")
        st.write("오른쪽 XML 내용을 확인해주세요.")
