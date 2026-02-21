#!/usr/bin/env python3
"""AppWorld 刷榜脚本 - 灵雀自主进化版 v4
使用智谱 API (GLM-4)
"""
import os
import json
import time
from pathlib import Path
from datetime import datetime
import requests

# AppWorld imports
from appworld import AppWorld, load_task_ids

# 配置
OUTPUT_DIR = Path("/home/ubuntu/projects/lingque/appworld_results")
OUTPUT_DIR.mkdir(exist_ok=True)

# 设置 AppWorld 数据路径
os.environ["APPWORLD_DATA_PATH"] = "/home/ubuntu/projects/lingque/data"

# 加载环境变量
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/projects/lingque/.env")

# 智谱 API 配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 测试集
TEST_NORMAL = load_task_ids("test_normal")
TEST_CHALLENGE = load_task_ids("test_challenge")

print(f"测试集: test_normal={len(TEST_NORMAL)}, test_challenge={len(TEST_CHALLENGE)}")

SYSTEM_PROMPT = """你是一个通过编写Python代码完成任务的AI助手。
代码将在一个包含多个应用API的环境中执行。

可用的应用包括: phone, venmo, gmail, amazon, spotify, splitwise, simple_note, todoist, file_system

规则:
1. 编写可执行的Python代码来解决问题
2. 使用 `apis.{app_name}.{api_name}(...)` 调用API
3. 完成后调用 `apis.supervisor.complete_task()`
4. 打印中间结果以跟踪进度
5. 优雅地处理错误"""

def call_glm4(prompt: str) -> str:
    """调用智谱 GLM-4 API"""
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "glm-4",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 2048
    }
    
    response = requests.post(ZHIPU_API_URL, headers=headers, json=data, timeout=60)
    response.raise_for_status()
    
    result = response.json()
    return result["choices"][0]["message"]["content"]

def solve_task_with_llm(world, instruction: str, max_turns: int = 10) -> dict:
    """使用 LLM 迭代解决任务"""
    execution_log = []
    
    for turn in range(max_turns):
        # 构建 prompt
        user_prompt = f"""任务: {instruction}

轮次 {turn + 1}/{max_turns}

之前的执行结果:
{chr(10).join(execution_log[-5:]) if execution_log else '无 - 这是第一轮'}

编写Python代码来解决这个任务。可用的API:
- apis.phone.* (联系人、消息、通话)
- apis.venmo.* (支付、好友)
- apis.gmail.* (邮件)
- apis.amazon.* (订单、购物车)
- apis.spotify.* (音乐、播放列表)
- apis.supervisor.complete_task() - 任务完成时调用

记住: 
1. 首先登录需要使用的应用
2. 打印结果以查看进展
3. 完成后调用 apis.supervisor.complete_task()"""

        # 调用 LLM
        code = call_glm4(user_prompt)
        
        # 提取代码块
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        
        # 执行代码
        try:
            exec_result = world.execute(code)
            execution_log.append(f"轮次 {turn + 1} 结果:\n{str(exec_result)[:1000]}")
            
            # 检查是否完成
            if world.task_completed():
                return {
                    "status": "completed",
                    "turns": turn + 1,
                    "execution_log": execution_log
                }
        except Exception as e:
            execution_log.append(f"轮次 {turn + 1} 错误: {str(e)}")
    
    return {
        "status": "max_turns_reached",
        "turns": max_turns,
        "execution_log": execution_log
    }

def run_single_task(task_id: str) -> dict:
    """运行单个任务"""
    try:
        world = AppWorld(task_id=task_id, experiment_name="lingque_glm4")
        instruction = world.task.instruction
        
        print(f"  Task: {instruction[:80]}...")
        
        start_time = time.time()
        result = solve_task_with_llm(world, instruction)
        elapsed = time.time() - start_time
        
        # 评估结果
        eval_result = world.evaluate()
        
        final_result = {
            "task_id": task_id,
            "instruction": instruction,
            "status": result["status"],
            "turns": result["turns"],
            "elapsed_seconds": elapsed,
            "success": eval_result.success if hasattr(eval_result, 'success') else False,
            "score": eval_result.score if hasattr(eval_result, 'score') else 0,
            "timestamp": datetime.now().isoformat()
        }
        
        world.close()
        return final_result
        
    except Exception as e:
        return {
            "task_id": task_id,
            "error": str(e),
            "status": "error",
            "timestamp": datetime.now().isoformat()
        }

def main():
    """主函数"""
    print(f"\n{'='*50}")
    print(f"AppWorld 刷榜开始 - {datetime.now()}")
    print(f"使用模型: GLM-4")
    print(f"目标: 从基线 42.4% 提升到 59.4%")
    print(f"{'='*50}\n")
    
    # 先跑 test_normal 前5个作为基线测试
    test_sample = TEST_NORMAL[:5]
    
    results = []
    for i, task_id in enumerate(test_sample):
        print(f"[{i+1}/{len(test_sample)}] Running {task_id}...")
        result = run_single_task(task_id)
        results.append(result)
        print(f"  -> {result['status']} (success={result.get('success', False)})")
        time.sleep(1)  # 避免 rate limit
    
    # 保存结果
    output_file = OUTPUT_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 统计
    completed = sum(1 for r in results if r["status"] == "completed")
    success = sum(1 for r in results if r.get("success", False))
    
    print(f"\n{'='*50}")
    print(f"本轮结果:")
    print(f"  完成率: {completed}/{len(results)} = {100*completed/len(results):.1f}%")
    print(f"  成功率: {success}/{len(results)} = {100*success/len(results):.1f}%")
    print(f"结果已保存: {output_file}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
