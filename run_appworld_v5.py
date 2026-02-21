#!/usr/bin/env python3
"""AppWorld 刷榜脚本 v5 - 正确使用 AppWorld API"""
import warnings
warnings.filterwarnings('ignore')

import os
import json
import time
from pathlib import Path
from datetime import datetime
import requests

os.environ["APPWORLD_DATA_PATH"] = "/home/ubuntu/projects/lingque/data"

from appworld import AppWorld, load_task_ids
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/projects/lingque/.env")

# 智谱 API
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 结果目录
OUTPUT_DIR = Path("/home/ubuntu/projects/lingque/appworld_results")
OUTPUT_DIR.mkdir(exist_ok=True)

# 加载测试集
TEST_NORMAL = load_task_ids("test_normal")
print(f"测试集: test_normal={len(TEST_NORMAL)}")

def call_llm(prompt: str, system: str = "") -> str:
    """调用智谱 GLM-4"""
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "glm-4",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048
    }
    
    resp = requests.post(ZHIPU_API_URL, headers=headers, json=data, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def solve_task(task_id: str, max_turns: int = 5) -> dict:
    """使用 LLM 解决单个任务"""
    world = AppWorld(task_id=task_id, experiment_name="lingque_v5")
    
    instruction = world.task.instruction
    allowed_apps = [a for a in world.task.allowed_apps if a not in ['api_docs', 'supervisor']]
    
    print(f"\n{'='*60}")
    print(f"Task: {task_id}")
    print(f"Instruction: {instruction}")
    print(f"Apps: {allowed_apps}")
    
    system_prompt = f"""你是一个 AI 助手，通过编写 Python 代码完成任务。

可用应用: {', '.join(allowed_apps)}

每个应用有不同的 API。例如:
- phone: add_contact, search_contacts, send_text_message, login, logout, show_profile
- venmo: add_friend, remove_friend, show_friends, send_money, login, show_profile
- gmail: send_email, show_inbox, login
- supervisor: complete_task() - 任务完成时必须调用

规则:
1. 先用 apis.{allowed_apps[0]}.login(username='...', password='...') 登录
2. 使用 apis.{allowed_apps[0]}.show_profile() 查看当前用户信息
3. 完成任务后调用 apis.supervisor.complete_task()
4. 每行代码都应该是可执行的 Python
5. 使用 print() 输出中间结果"""

    conversation = []
    
    for turn in range(max_turns):
        # 构建 prompt
        history = "\n".join([f"Turn {i+1}:\n{c['code']}\nResult: {c['result']}" 
                            for i, c in enumerate(conversation[-3:])])
        
        prompt = f"""任务: {instruction}

{"之前的历史:" + chr(10) + history if conversation else "这是第一轮。"}

请编写 Python 代码来完成任务。只输出代码，不要解释。"""

        # 获取 LLM 代码
        llm_response = call_llm(prompt, system_prompt)
        
        # 提取代码块
        code = llm_response
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        code = code.strip()
        
        print(f"\n--- Turn {turn+1} ---")
        print(f"Code:\n{code[:300]}...")
        
        # 执行代码
        result = world.execute(code)
        print(f"Result: {result[:200] if result else 'None'}...")
        
        conversation.append({"code": code, "result": result})
        
        # 检查是否完成
        if "complete_task" in code.lower() or "success" in result.lower():
            break
    
    # 评估
    world.close()
    
    return {
        "task_id": task_id,
        "turns": len(conversation),
        "completed": "complete_task" in str(conversation[-1])
    }

def main():
    print(f"开始 AppWorld 刷榜 - {datetime.now()}")
    print(f"测试 {len(TEST_NORMAL)} 个任务")
    
    results = []
    
    # 先测试前5个
    for i, task_id in enumerate(TEST_NORMAL[:5]):
        print(f"\n[{i+1}/5]")
        try:
            result = solve_task(task_id)
            results.append(result)
        except Exception as e:
            print(f"Error: {e}")
            results.append({"task_id": task_id, "error": str(e)})
    
    # 保存结果
    result_file = OUTPUT_DIR / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n\n结果保存到: {result_file}")
    
    # 统计
    completed = sum(1 for r in results if r.get("completed"))
    print(f"完成率: {completed}/{len(results)} = {completed/len(results)*100:.1f}%")

if __name__ == "__main__":
    main()
