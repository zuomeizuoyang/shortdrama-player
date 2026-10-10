# 参与贡献

欢迎通过 Issue 报告问题，或提交 Pull Request。

报告平台故障时，请附平台名称、操作步骤、内容语言、Python 版本与错误信息。请删除 Cookie、API Key、设备信息和临时媒体链接。

修改适配器时，请保持 `feed/search/detail/episodes/resolvePlayback` 的统一返回结构；有榜单能力的平台还提供 `rank`。受限章节应保留访问状态和官方入口。

提交前运行：

```shell
python -m pip install -r requirements.txt
python -m unittest discover -s . -t . -p "test_*.py" -v
node --check static/app.js
```

Node.js 仅用于贡献时的 JavaScript 语法检查，运行播放器无需安装。

联网检查脚本是人工验证工具，不在 CI 中运行；生成的验证文件不提交。提交代码即表示同意以本项目的 MIT 许可发布你的贡献。
