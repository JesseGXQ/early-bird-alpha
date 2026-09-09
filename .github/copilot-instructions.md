# Project Guidelines

## Response Style

**默认只分析，不动代码。** 当用户提出问题或讨论时，仅提供：
- 分析和诊断
- 观点和建议
- 解决方案思路

**除非用户明确指示**（如"请修改"、"帮我改"、"修复这个"、"implement this"等），否则不要对代码做任何改动（包括编辑文件、创建文件等）。即使用户指出了代码中的问题，也只给出分析和修复建议，等待用户明确要求修改后再动手。

终端命令（运行测试、构建、安装依赖等）不受此限制，可根据分析需要自行执行。

## MCP 使用说明

* pylanceRunCodeSnippet: workspaceRoot is required for the pylanceRunCodeSnippet MCP tool. Pass workspaceRoot or configure a default workspace root.
