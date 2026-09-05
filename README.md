# 龙族：卡塞尔之门 · 命轮助手（longzu-minglun-assistant）

玩家自制的命运之轮（命轮盘）规划工具：录入命轮碎片库存、自选碎片与限定角色碎片，一键生成 **MILP 认证最优** 的点亮/升星/进阶方案。支持移动端与 PC 双布局。

**在线体验**：http://101.132.145.199/longzu/minglun/v2/ · **仓库**：https://github.com/ZoeyHao/longzu-minglun-assistant

> 本项目与游戏官方无关，仅供玩家交流使用。

## 功能

- **库存录入**：逐角色填写，支持三种自动填充方式（可叠加使用，最终汇入同一张表）：
  - 口述文本解析（如「恺撒56，陈墨瞳40，……碎片方面：……」）
  - 命盘「物品详情」弹窗截图识别（图片上传至本站 API，写入临时文件完成模板匹配与数字 OCR，处理后立即删除）
  - 手动微调（解析结果都可人工修正）
- **角色碎片 ∞ 开关**：常驻 SSR 默认无限、限定 SSR 默认按 0，逐角色可切换
- **MILP 求解**：按字典序目标链（如 精神元素 → 精神元素% → 消耗 → …）逐目标认证最优，输出 `optimal` 即数学证明的全局最优
- **自选碎片分配**：按缺口（需求 − 库存）取 top5 分配；含限定 SSR/UR 的组合按可进阶轮次计需求，无角色碎片 = 不能进阶只计第 1 轮
- **私有历史方案**：计算无需昵称；用户主动保存后，服务器仅向当前浏览器的私有访问令牌返回记录，支持查看与一键载入库存
- **数据明细**：198 个组合全表（元素/轮盘筛选 + 搜索），支持下载 Excel

## 计算口径

- 品阶 白→绿→蓝→紫→橙→彩，彩 0 星为终点，共 5 次进阶；每次进阶星级归零重新升星
- 基础效果按每升 1 星累计；进阶效果每完成一次真实进阶计一次（当前仅精神元素页提供）
- 进阶角色碎片成本：绘梨衣 15/次，其余限定 SSR 与 UR 30/次；常驻 SSR / SR / R 默认视为无限
- 所有组合默认从白 0 星开始规划

> 当前版本还不能录入各组合已经达到的品阶/星级。老账号应把结果理解为“以现有剩余库存重新从白 0 星建模”的理论方案，而不是精确的跨阶结算。

## 本地运行

```bash
# 前端
npm install
npm run dev

# 求解引擎依赖 Python（highspy / openpyxl / pillow / numpy）
pip install -r requirements.txt
# 如系统 python3 不含依赖，用环境变量指定：
# MINGLUN_PYTHON=/path/to/python3 npm run dev
```

打开 `http://localhost:3000` 即可使用。开发模式下 API 由 `vite.config.ts` 中间件提供（spawn Python 子进程）。

### 角色头像（可选）

版权原因，角色头像（`public/avatars/`）**不随仓库发布**。缺失时不影响计算与识别，仅库存表不显示头像。如需显示，请自行从游戏截图获取并按 `public/avatars/<角色名>.png` 放置（角色名以 `server/skill/references/avatar-map.json` 为准）。

## 测试

```bash
npm run lint
npm run build
python3 server/test_engine.py   # 自选缺口与进阶效果口径单元测试
python3 server/test_api_server.py  # 历史方案隔离与保留策略
```

## 服务器部署（生产）

- `server/api_server.py`：轻量 HTTP 服务（默认 127.0.0.1:8321），路由：
  - `GET /api/game-data` 游戏数据 · `GET /api/combos` 组合明细 · `GET /api/combos.xlsx` Excel 导出
  - `POST /api/plan` 求解 · `POST /api/recognize` 截图识别
  - `GET/POST/DELETE /api/history` 私有历史方案（请求必须携带浏览器生成的 `X-Minglun-Owner`；服务端只保存其 SHA-256）
- 前端静态构建：`npx vite build --base=<你的部署路径>/`
- 建议 nginx 反代 `/api/` 到 8321，systemd 托管 `api_server.py`；环境变量：`MINGLUN_SKILL_DIR`（数据目录，默认 `server/skill`）、`MINGLUN_RECOGNIZER_DIR`、`MINGLUN_PORT`
- 上线必须启用 HTTPS；可从 `deploy/nginx-minglun.conf.example` 和 `deploy/minglun-api.service.example` 开始配置，并替换示例域名、证书和路径
- API 内置请求体上限、图片格式/像素校验、每 IP 基础频率限制和昂贵任务并发限制；nginx 限流与 systemd 资源配额是必须保留的第二层防护
- 历史方案单条最多 256 KB、每浏览器最多 4 MB、全站最多 16 MB；建议将 `MINGLUN_HISTORY_FILE` 指向仅服务账号可读写的 `/var/lib` 路径
- `MINGLUN_ALLOWED_ORIGINS` 可配置逗号分隔的正式站点 Origin；生产环境应设置为 HTTPS 域名

## 目录结构

```
src/                 React + TS + Tailwind 前端
server/              Python 后端（求解引擎 / API / 明细导出）
  engine.py          MILP 引擎（逐目标字典序认证最优）
  test_engine.py     单元测试
  skill/             命轮数据与查询脚本（工作簿 / 卡池 / 补充组合）
  recognizer/        背包截图离线识别器（模板匹配 + OCR）
```

## 致谢

- 数据支持：君度
- 技术支持：Roy、梧桐落

## 许可

代码以 [MIT](LICENSE) 发布。游戏相关数据与素材的版权归原作者及《龙族：卡塞尔之门》权利方所有，不随 MIT 许可授权；如有权属问题请提 Issue 联系删除。
