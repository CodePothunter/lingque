#!/usr/bin/env python3
"""AppWorld 刷榜脚本 - 灵雀自主进化版 v3
基于 AppWorld 官方 API execute() 方法
"""
import os
import json
import time
import anthropic
from pathlib import Path
from datetime import datetime

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

# 初始化 Anthropic 客户端（通过智谱代理）
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    base_url=os.getenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
)

# 测试集
TEST_NORMAL = load_task_ids("test_normal")
TEST_CHALLENGE = load_task_ids("test_challenge")

print(f"测试集: test_normal={len(TEST_NORMAL)}, test_challenge={len(TEST_CHALLENGE)}")

SYSTEM_PROMPT = """You are an AI assistant that completes tasks by writing Python code.
The code will be executed in an environment where APIs for various apps are available.

Available apps include: phone, venmo, gmail, amazon, spotify, splitwise, simple_note, todoist, file_system

Rules:
1. Write executable Python code to solve the task
2. Use `apis.{app_name}.{api_name}(...)` to call APIs
3. Always end with `apis.supervisor.complete_task()` when done
4. Print intermediate results to track progress
5. Handle errors gracefully"""

def solve_task_with_llm(world, instruction: str, max_turns: int = 10) -> dict:
    """使用 LLM 迭代解决任务"""
    conversation = []
    execution_log = []
    
    for turn in range(max_turns):
        # 构建 prompt
        user_prompt = f"""Task: {instruction}

Turn {turn + 1}/{max_turns}

Previous execution results:
{chr(10).join(execution_log[-5:]) if execution_log else 'None - this is the first turn'}

Write Python code to solve this task. Available APIs:
- apis.phone.* (contacts, messages, calls)
- apis.venmo.* (payments, friends)
- apis.gmail.* (emails)
- apis.amazon.* (orders, cart)
- apis.spotify.* (music, playlists)
- apis.supervisor.complete_task() - call this when task is complete

Remember: 
1. First login to any app you need to use
2. Print results to see what's happening
3. Call apis.supervisor.complete_task() when done"""

        # 调用 LLM
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        code = response.content[0].text
        
        # 提取代码块
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        
        # 执行代码
        try:
            exec_result = world.execute(code)
            execution_log.append(f"Turn {turn + 1} result:\n{exec_result}")
            
            # 检查是否完成
            if world.task_completed():
                return {
                    "status": "completed",
                    "turns": turn + 1,
                    "execution_log": execution_log
                }
        except Exception as e:
            execution_log.append(f"Turn {turn + 1} error: {str(e)}")
    
    return {
        "status": "max_turns_reached",
        "turns": max_turns,
        "execution_log": execution_log
    }

def run_single_task(task_id: str) -> dict:
    """运行单个任务"""
    try:
        world = AppWorld(task_id=task_id, experiment_name="lingque_baseline")
        instruction = world.task.instruction
        
        print(f"  Task: {instruction[:100]}...")
        
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
        time.sleep(2)  # 避免 rate limit
    
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
