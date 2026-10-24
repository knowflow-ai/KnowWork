# 参与贡献

谢谢你愿意改进 KnowWork。

## 开始之前

- 修 bug、补文档可以直接提 PR；新功能或较大的改动，请先开 issue 说清楚要解决什么问题，免得做完了方向对不上。
- 开发环境、检查命令与代码约定见 [guide/development.md](guide/development.md)。

## 提交 PR

- 一个 PR 做一件事。
- 提交信息写成 `<类型>: <中文描述>`，类型用 feat、fix、refactor、docs、test、chore、perf、ci、style，需要时正文用 `-` 列要点。
- 提交前把改动涉及部分的检查跑过（ruff 与 pytest、宿主的 npm test、前端的 lint、test、build）。
- 改了表结构要配迁移；改了行为要配测试；改了部署或配置要同步 `guide/` 里的文档。
- 照搬了别的开源项目的实现，在注释里写明来源，并确认许可证兼容。

本仓库的提交历史按版本同步，不是逐个提交；你的 PR 合并后会出现在下一个版本里，贡献者署名保留在 PR 上。

## 许可

提交贡献即表示你同意以 [Apache License 2.0](LICENSE) 授权你的贡献。

## 安全问题

请不要在公开 issue 里报告安全漏洞，按 [SECURITY.md](SECURITY.md) 私下报告。
