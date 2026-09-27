from openai import OpenAI
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

messages = [
    {"role": "system", "content": "너는 공공기관 문서 작성 도우미야. 개조식으로 작성해."}
]

def chat(user_input):
    messages.append({"role": "user", "content": user_input})
    res = client.chat.completions.create(
        model="google/gemma-4-e4b", temperature=0.3, messages=messages
    )
    answer = res.choices[0].message.content
    messages.append({"role": "assistant", "content": answer})  # 답변도 기록에 추가
    return answer

print("--- 1st prompt start ---")
print(chat("회의 결과 보고서 초안 써줘: 웹 기반 개발 결정, 참석 5명"))
print("--- 1st prompt end ---")

print("--- 2nd prompt start ---")
print(chat("방금 쓴 거에서 '향후 계획' 항목 추가해줘"))  # 앞 대화를 기억하는 것처럼 동작
print("--- 2nd prompt end ---")

