# smart-color-remover

智能颜色抠图器（Smart Color Remover）—— 把图片里指定颜色变成透明，生成带 Alpha 通道的 PNG。

输入一张图片 + 颜色描述（如「扣掉红色」「去掉白色背景」「#00FF00 抠成透明」），自动识别并匹配该颜色，将其透明化。

## 特性

- **中文 / 英文颜色名**：`红` `橙` `黄` `绿` `青` `蓝` `紫` `粉` `品红` `棕` `黑` `白` `灰` `金` `银` `米`，及 `light blue` `dark green` 等英文别名
- **深浅修饰词**：`浅红` `淡蓝` `深绿` `暗棕` `亮黄` `鲜艳橙` —— 自动调整明度 / 饱和度匹配窗口
- **精确值**：`#FF0000` / `#F00` / `rgb(255,0,0)` / `255,0,0`，走感知加权 RGB 距离匹配（只抠相近色）
- **HSV 色相区间匹配**：「红」能覆盖大红、深红、暗红、粉红等整个红色系
- **`--background-only`**：只抠与图像边缘连通的区域，保护物体内部同色区域（依赖 scipy）
- **`--invert`**：反选，只保留指定颜色，其余变透明
- **`--feather`**：边缘高斯羽化，平滑抗锯齿边缘
- **`--min-region`**：剔除小于 N 像素的孤立噪点（依赖 scipy）
- 自动清洗「扣掉 / 去掉 / 背景 / 透明」等口语干扰词；JPEG 输入正常支持

## 安装（作为 WorkBuddy / CodeBuddy skill）

将本仓库解压到 skills 目录即可：

```bash
# WorkBuddy
unzip smart-color-remover.zip -d ~/.workbuddy/skills/
# 或 CodeBuddy
unzip smart-color-remover.zip -d ~/.codebuddy/skills/
```

## 独立使用

```bash
pip install pillow numpy scipy   # scipy 仅 --background-only / --min-region 需要
python3 scripts/color_remover.py <输入图片> --color "<颜色>" [选项]
```

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--color, -c` | 要抠掉的颜色（必填） | - |
| `--output, -o` | 输出 PNG 路径 | `<原名>_transparent.png` |
| `--tolerance, -t` | 容差 0-100，越大抠得越宽 | 25 |
| `--feather, -f` | 边缘羽化半径 0-5 | 0 |
| `--invert` | 反选：保留该颜色，其余透明 | 关 |
| `--background-only` | 只抠与边缘连通的区域 | 关 |
| `--min-region` | 移除小于 N 像素的孤立噪点 | 0 |
| `--json` | 以 JSON 输出统计 | 关 |

### 示例

```bash
# 扣掉红色（整个红色系）
python3 scripts/color_remover.py banner.png --color "红色"

# 去掉白色背景，只抠和边缘连通的背景 + 羽化平滑
python3 scripts/color_remover.py product.jpg --color "白色" --background-only -f 1

# 扣掉浅蓝色，容差调大
python3 scripts/color_remover.py sky.png --color "浅蓝" -t 40 -f 2

# 精确抠 #00A651 附近颜色
python3 scripts/color_remover.py logo.png --color "#00A651" -t 15

# 只保留红色，其余全部透明
python3 scripts/color_remover.py poster.png --color "红" --invert
```

## 实现说明

- 颜色名走 **HSV 色相区间匹配**（有彩色）或 **亮度主导匹配**（黑 / 白 / 灰 / 银），同色系深浅变体一并覆盖
- 精确值走 **感知加权 RGB 欧氏距离**（`2·dr² + 4·dg² + 3·db²`），只对相近色生效
- 输出强制 PNG 以保留透明通道

## License

MIT © flybirp
