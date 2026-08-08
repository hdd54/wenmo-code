"""天气插件：提供简单的天气查询功能。"""


def get_weather_handler(arguments: dict) -> dict:
    """返回当前天气信息：晴天，25 度。"""
    return {
        "weather": "晴天",
        "temperature": "25 度",
        "description": "晴天，25 度",
    }


PLUGIN_TOOLS = [
    {
        "name": "get_weather",
        "description": "获取当前天气信息（晴天，25 度）",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "handler": get_weather_handler,
    }
]
