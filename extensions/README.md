# 问墨扩展包（DLC）

把一个扩展目录放到这里，开发版会自动发现。客户端用户扩展目录为
`%APPDATA%\问墨\content\extensions`，开发目录不会进入客户端安装包。

最小结构：

```text
my-dlc/
  wenmo-extension.json
  plugins/       # 可选，普通问墨插件 .py
  skills/        # 可选，每个技能目录内含 SKILL.md
  mcp.json       # 可选，结构与主 mcp.json 的 servers 相同
```

清单示例：

```json
{
  "name": "my-dlc",
  "version": "1.0.0",
  "enabled": true
}
```

复制目录即安装，删除目录即卸载。密钥写入忽略版本控制的本地配置，禁止放入扩展包。
