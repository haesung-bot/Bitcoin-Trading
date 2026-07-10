import sqlite3

# 장부 파일 연결
conn = sqlite3.connect("license_codes.db")

try:
    # 1. 텔레그램 유저 체험판 중복 제한 풀기
    conn.execute("DELETE FROM trial_users WHERE telegram_user_id='8895506329'")
    
    # 2. 해당 유저의 기기 묶임(막힘) 풀기 및 차단 해제
    conn.execute("UPDATE license_codes SET bound_device_id=NULL, is_blocked=0 WHERE code='57A9-72ED-3901'")
    
    # 3. 장부 저장 및 닫기
    conn.commit()
    print("🎉 유저 초기화 및 기기 해제가 완벽하게 완료되었습니다!")
except Exception as e:
    print(f"오류 발생: {e}")
finally:
    conn.close()