<p align="center">
  <img src="web/public/brand/knowwork-logo.svg" alt="KnowWork" height="56">
</p>

<p align="center">
  An enterprise, self-hosted Agent platform built on DeepSeek Harness
</p>

<p align="center">
  <a href="README.md">简体中文</a> | English
</p>

---

> **DSH decides how an agent runs. KnowWork decides how an agent gets into an enterprise's self-hosted production environment.**

## Why KnowWork

We believe enterprise agents for vertical industries are about to take off. Contract review, policy Q&A, report
preparation, ticket handling: every industry will have its own set of agents built around its business. To get into an
enterprise, all of them have to solve the same problems.

- Data and model keys cannot leave the company's data center, so the agent has to run on the company's own servers.
- Who may use which model, knowledge base and tool has to follow the company's organization chart.
- The commands and file operations an agent performs have to be isolated from everything else on the server.
- Write operations need human approval; every step has to be auditable and replayable; usage has to be accountable.

Building this layer from scratch costs every team a lot of time, and the business itself gets pushed aside. KnowWork
builds that layer once. It uses [DeepSeek Harness (DSH)](https://www.npmjs.com/package/@deepseek-ai/dsh) as the agent
kernel and adds what a self-hosted enterprise production environment needs: accounts and permissions, model and key
management, per-session sandboxes, approvals, audit and usage. Teams building enterprise agents on KnowWork can focus
on their business: writing skills, connecting knowledge bases, connecting tools.

**Web only, no desktop app.** A self-hosted enterprise deployment is one server shared by many people, and
permissions, models and audit are managed centrally by administrators. Employees open a browser and start working;
nothing has to be installed on each computer.

**Continuous compatibility with DSH.** KnowWork uses only DSH's official extension points (profiles, plugins, skill
providers, tool restriction and so on) and does not fork DSH. In practice:

- all `@deepseek-ai/*` packages are pinned to the same version (currently `0.2.1-rc.2`);
- before an upgrade, a preflight check and admission tests run (create a session, run, cancel, resume, load skills,
  subagents, the tool bridge and more), and the version is switched only when they pass;
- new DSH releases are followed, and capabilities that ship with DSH are picked up directly.

KnowWork belongs to the same product family as [KnowFlow](https://github.com/knowflow-ai/KnowFlow): KnowFlow manages
knowledge, KnowWork gets the work done.

The current version is v0.0.1, the first public release. APIs and data structures may still change before 1.0.

## Features

### Conversations and execution

| Feature | Description |
|---|---|
| Visible execution | Every step the model takes is streamed: which tool it called, with what arguments and what came back. Collapsed by default, expandable at any time |
| Follow-ups and cancel | Enter queues a message until the current task finishes; a key combination adds it to the running task. Cancel at any time |
| Tasks keep running | Close the browser and the task still finishes on the server; sessions resume automatically after the runtime host restarts |
| Native DSH features | Subagents, to-do lists, background jobs and questions to the user are shown and answered in the web UI |
| Attachments | Uploaded files are placed in the session sandbox for the model to read; PDF and Office files are parsed to Markdown by MinerU |
| Traceable citations | Citation numbers in an answer can only point to evidence a tool actually returned; click to see the source |
| Skill invocation | Type `/skill-name` to invoke a skill explicitly, or let the model load it when needed |
| Answer language | Choose Chinese or English; otherwise it follows the question |
| Run details | Step through your own turn: model output, tool calls, failures and retries |

### Session sandbox

| Feature | Description |
|---|---|
| One container per session | The model's commands and file operations stay inside the session's own Docker container, away from the server's database, keys and other people's data |
| Hardening | All Linux capabilities dropped, privilege escalation blocked; network access can be turned off; runtimes such as gVisor can be used |
| Office toolchain | LibreOffice, docx-js, pptxgenjs, pandas, matplotlib and more are built in. Word, Excel and PowerPoint files can be created, and existing files edited with tracked changes, comments and formula recalculation |
| Automatic cleanup | Idle containers are reclaimed; attachments are put back when the session is used again |

### Delivered files

| Feature | Description |
|---|---|
| Explicit delivery | Only files the model delivers with DSH's official `present` tool show up in the conversation; temporary files do not |
| Online preview | Word, Excel, PDF, Markdown, CSV and more can be previewed; PDF text is selectable and zoomable |
| Safe download | Permissions are checked on every download; exported spreadsheets are protected against formula injection |

### Skills

| Feature | Description |
|---|---|
| Skill packages | Upload a ZIP with `SKILL.md` and its resources; required environment variables can be declared |
| Draft and publish | Published versions are numbered; personal and workspace skills; skills can be disabled |
| Built-in Office skills | Word, Excel and PowerPoint skills ship with the product |
| Completion criteria | A skill can declare tools that must succeed; until they do, the model cannot give a final answer |
| Tool unlocking | Specialist tools claimed by a skill stay hidden from the model until the skill is loaded |
| Run records | Every run records the version, how it was invoked, each step's result, duration, usage and failure stage; success rate per version, with a step-by-step view of each turn |

### Knowledge and tools

| Feature | Description |
|---|---|
| KnowFlow knowledge bases | Authorized per knowledge base; only bases the asker may use are searched, and citations lead back to the source |
| MCP | Administrators connect MCP servers and grant them to users, groups and departments |
| Approvals | Write and high-risk tool calls are approved by the person who started the turn, with the option to allow the same kind of action for the rest of the session |
| Action receipts | Each tool call is recorded once per action, so retries never repeat a write |
| Hidden credentials | Tool and model credentials stay in the business service; neither the model nor the runtime host can see them |

### Models

| Feature | Description |
|---|---|
| Model catalog | Any model with an OpenAI-compatible API (DeepSeek, Qwen, self-hosted vLLM and others), with a connectivity test |
| Groups and grants | Models are granted in groups to users, user groups and departments |
| Model gateway | The runtime host calls models through the business service with short-lived tokens; keys are stored encrypted; calls per turn are capped to stop runaway loops |
| Usage and cost | Tokens and cost per user and per model |

### Organization, permissions and audit

| Feature | Description |
|---|---|
| Organization | Members, invitations, a department tree, user groups |
| Roles and grants | Four roles per workspace; skills, tools, knowledge bases and model groups can be granted to users, groups and departments, inherited down the department tree |
| Data isolation | PostgreSQL row-level security per workspace; the application account does not own the tables |
| Audit | Human write operations and agent tool calls are recorded separately and can be searched |
| Run history | Administrators browse every turn in the workspace by time, member and status; viewing someone else's run is itself audited |

### Accounts and system

| Feature | Description |
|---|---|
| Sign-in | Password, invitation sign-up, OIDC single sign-on; failed sign-ins are rate limited |
| API keys | Personal API keys are stored only as hashes, limited in scope, with expiry and revocation |
| Branding | The product name and logo can be replaced in system settings |

### Deployment and operations

| Feature | Description |
|---|---|
| One-command install | `install.sh` checks the environment, writes the configuration, pulls images, starts everything and waits until it is ready |
| Works in mainland China | All images, including PostgreSQL, Redis and RustFS, are hosted on Alibaba Cloud Container Registry |
| Automatic migrations | The database is upgraded when the business service starts, under a lock |
| Generated secrets | A deployment master key is generated automatically and all other credentials are derived from it |
| Health check | `/ready` reports whether the database and the runtime host are healthy |

### Planned

A skill test bench (cases, structural assertions, draft versus published comparison), scenario packs, a UI for fixed
workflows, backup and restore, password change and account disabling.

## Quick start

You need a Linux (x86_64) server with Docker 24+ and Compose v2.24+. The session sandbox is the largest image at about
2.2 GB, so leave enough disk space.

```sh
git clone https://github.com/knowflow-ai/KnowWork.git
cd KnowWork
deploy/install.sh
```

The script writes `deploy/.env`, pulls images from Alibaba Cloud Container Registry, starts the services, waits until
they are ready and prints the address to open. On a cloud server the public IP is usually not on the network interface:
pass it with `deploy/install.sh --url http://<public-ip>:8080` and open port 8080 in the security group. Then:

1. follow the setup wizard to create the administrator;
2. go to Admin → Models, add a provider and a model, and set the model alias to `default`;
3. go back to the workspace and start your first task.

> The session sandbox is on by default. It mounts the host's `docker.sock` into the business service, which amounts to
> root on the host, so it is meant for single-server self-hosted deployments only. If you do not need command
> execution or file generation, turn it off with `deploy/install.sh --no-sandbox`.

The documentation under `guide/` is in Chinese: deployment options, HTTPS and upgrades in
[guide/deploy.md](guide/deploy.md), configuration in [guide/configuration.md](guide/configuration.md).

## Architecture

```text
Browser ──> Gateway (nginx) ──> Business service (Python, FastAPI) ──> PostgreSQL / Redis / object storage
                                     │  ▲
                    kernel contract  │  │  short-lived token callbacks: model calls, tool execution,
                         kernel.v1   ▼  │  skill downloads, sandbox
                               Runtime host (Node, DSH) ──> Session sandbox (one Docker container per session)
```

- The browser only talks to the business service. Accounts, permissions, model keys and audit live there; DSH holds
  no keys and makes no permission decisions.
- The runtime host assembles agents with DSH's official profiles and plugin extension points; model calls and tool
  execution go back to the business service with short-lived tokens.
- The business service calls the runtime host only through a versioned kernel contract, so DSH upgrades stay on the
  runtime host side.

See [guide/architecture.md](guide/architecture.md).

## Contributing

One command starts the whole stack locally (embedded PostgreSQL, runtime host, business service, frontend); see
[guide/development.md](guide/development.md). Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting, and
report security issues privately as described in [SECURITY.md](SECURITY.md).

## License and acknowledgements

KnowWork is released under the [Apache License 2.0](LICENSE).

- [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh), the agent kernel (MIT).
- [WeKnora](https://github.com/Tencent/WeKnora); the Docker backend of the session sandbox and the deployment layout
  follow its approach (MIT).
- [@openai/apps-sdk-ui](https://www.npmjs.com/package/@openai/apps-sdk-ui), UI components (MIT).

Third-party notices are in [NOTICE](NOTICE).
