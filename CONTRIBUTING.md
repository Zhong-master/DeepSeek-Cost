# 贡献指南

欢迎提 Issue / PR。这个项目刻意保持“小而原生”：不引入第三方 pip 依赖，只用标准库 + PyGObject。

## 开发环境

```bash
sudo apt-get install -y python3-gi python3-gi-cairo python3-pil \
    gir1.2-gtk-3.0 gir1.2-pango-1.0 \
    gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1 \
    gir1.2-notify-0.7 gir1.2-webkit2-4.1 ffmpeg

git clone https://github.com/Zhong-master/DeepSeek-Cost.git && cd DeepSeek-Cost
```

不需要登录真实账号：测试全部走本地假接口 `tests/mock_server.py`。

## 跑测试

```bash
bash tests/run_tests.sh       # 59 个单元/集成用例，全离线
python3 tests/smoke_gui.py    # 32 项 GUI 端到端检查（需要 DISPLAY + ffmpeg）
python3 -m flake8 --select=F,E9 --max-line-length=130 src tests
```

CI（`.github/workflows/tests.yml`）会在 ubuntu-24.04 上跑这两套。

## 代码约定

* 注释/日志用中文，标识符用英文；公开函数写 docstring。
* 所有 GTK 调用只在主线程；网络请求放子线程，结果用 `GLib.idle_add` 回到主线程。
* 新增/修改涉及取数的逻辑，请在 `tests/mock_server.py` 里加对应的假接口，并补测试。
* 不要在日志、异常信息、测试输出里打印任何凭据（token / API Key）。
* 改动面板显示相关内容时，注意「宽度 ≥ 1.5 × 高度」这个前提（见 `render.py` 的注释）。

## 提交 PR

1. 保持提交信息清晰（推荐 `fix:` / `feat:` / `docs:` 前缀）。
2. 确保 `tests/run_tests.sh` 与 `tests/smoke_gui.py` 全绿。
3. 涉及用户可见行为的改动，请同时更新 `README.md` 与 `CHANGELOG.md`。

## 发布新版本（维护者）

1. 改 `src/deepseek_cost/__init__.py` 里的 `__version__`，并在 `CHANGELOG.md` 记录。
2. 打 tag 并推送：

   ```bash
   git tag v<版本> && git push --tags
   ```

3. `.github/workflows/release.yml` 会自动构建 `.deb`、生成 `SHA256SUMS.txt` 并发布到 Release，
   下载地址为 `https://github.com/Zhong-master/DeepSeek-Cost/releases/latest/download/deepseek-cost_<版本>_all.deb`。
   本地想先验证的话，`./build-deb.sh` 的效果与 CI 一致。
