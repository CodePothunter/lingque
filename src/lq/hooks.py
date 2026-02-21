"""
灵雀框架生命周期钩子系统

提供5个钩子：
- on_load: Agent 启动时
- on_heartbeat: 心跳触发时
- on_message: 收到消息时
- on_reply: 发送回复前
- on_shutdown: Agent 关闭时

安全特性：
- 异常隔离：单个钩子失败不影响其他钩子
- 执行超时：每个钩子最多执行5秒，防止阻塞

使用方法：
    from lq.hooks import hooks
    
    @hooks.register("on_load")
    async def my_init_hook(gateway):
        # 初始化逻辑
        pass
"""

from typing import Callable, Any
import logging
import asyncio

logger = logging.getLogger(__name__)

# 默认钩子执行超时时间（秒）
DEFAULT_HOOK_TIMEOUT = 5.0


class HookTimeoutError(Exception):
    """钩子执行超时异常"""
    pass


class HookRegistry:
    """钩子注册中心"""
    
    def __init__(self, timeout: float = DEFAULT_HOOK_TIMEOUT):
        """
        Args:
            timeout: 单个钩子最大执行时间（秒），默认5秒
        """
        self._hooks: dict[str, list[Callable]] = {
            "on_load": [],
            "on_heartbeat": [],
            "on_message": [],
            "on_reply": [],
            "on_shutdown": [],
        }
        self._timeout = timeout
    
    def register(self, hook_name: str):
        """装饰器：注册钩子
        
        Args:
            hook_name: 钩子名称，必须是 on_load/on_heartbeat/on_message/on_reply/on_shutdown 之一
        
        Returns:
            装饰器函数
        
        用法：
            @hooks.register("on_load")
            async def my_hook(gateway):
                # 初始化逻辑
                pass
        """
        def decorator(func: Callable):
            if hook_name in self._hooks:
                self._hooks[hook_name].append(func)
                logger.info("注册钩子: %s -> %s", hook_name, func.__name__)
            else:
                logger.warning("未知钩子名称: %s（有效值：%s）", hook_name, list(self._hooks.keys()))
            return func
        return decorator
    
    async def trigger(self, hook_name: str, *args, **kwargs) -> list[Any]:
        """触发钩子（带超时保护）
        
        Args:
            hook_name: 钩子名称
            *args: 传递给钩子函数的位置参数
            **kwargs: 传递给钩子函数的关键字参数
        
        Returns:
            所有钩子函数的返回值列表
        
        安全特性：
            1. 异常隔离：单个钩子失败不影响其他钩子
            2. 执行超时：每个钩子最多执行 self._timeout 秒
        """
        results = []
        hook_funcs = self._hooks.get(hook_name, [])
        
        for func in hook_funcs:
            try:
                # 包装同步函数为异步
                if asyncio.iscoroutinefunction(func):
                    coro = func(*args, **kwargs)
                else:
                    coro = asyncio.to_thread(func, *args, **kwargs)
                
                # 执行带超时保护
                result = await asyncio.wait_for(coro, timeout=self._timeout)
                results.append(result)
                
            except asyncio.TimeoutError:
                logger.error(
                    "钩子执行超时: %s.%s（超时时间: %.1f秒）",
                    hook_name, func.__name__, self._timeout
                )
            except Exception:
                logger.exception("钩子执行失败: %s.%s", hook_name, func.__name__)
        
        logger.debug(
            "钩子 %s 执行完成，共 %d 个，返回 %d 个结果",
            hook_name, len(hook_funcs), len(results)
        )
        return results
    
    def list_hooks(self) -> dict[str, list[str]]:
        """列出所有已注册的钩子
        
        Returns:
            字典，key 是钩子名称，value 是已注册的函数名列表
        """
        return {
            name: [func.__name__ for func in funcs]
            for name, funcs in self._hooks.items()
        }
    
    def clear(self, hook_name: str = None):
        """清除钩子注册
        
        Args:
            hook_name: 钩子名称，如果为 None 则清除所有钩子
        """
        if hook_name is None:
            for name in self._hooks:
                self._hooks[name] = []
            logger.info("清除所有钩子注册")
        elif hook_name in self._hooks:
            self._hooks[hook_name] = []
            logger.info("清除钩子: %s", hook_name)
        else:
            logger.warning("未知钩子名称: %s", hook_name)


# 全局单例
hooks = HookRegistry()
