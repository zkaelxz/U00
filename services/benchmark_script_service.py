"""
services/benchmark_script_service.py -- counts how much of a Chinese transcript is
Traditional vs Simplified, for the Benchmark Lab. UI-free.

CER in the Lab is blind to script, so this is the only place a Traditional-writing
model shows apart from a Simplified-writing one. Only characters that exist in one
script alone are counted as evidence; shared characters are neutral.
"""
# Each token is a pair "simplified+traditional". Only pairs whose Simplified form is
# not also in ordinary Traditional use and whose Traditional form is not in ordinary
# Simplified use belong here (so 后/後, 发/發/髮, 干, 面/麵, 里/裡 are left out): a
# listed character is then proof of its script.
_PAIRS = """
这這 们們 个個 来來 为為 说說 会會 时時 对對 经經 过過 没沒 还還 国國 学學 见見 开開 无無 现現
动動 样樣 长長 头頭 问問 间間 机機 实實 点點 门門 车車 马馬 鸟鳥 鱼魚 书書 买買 卖賣 红紅 绿綠
蓝藍 东東 爱愛 听聽 吗嗎 让讓 谁誰 谢謝 请請 认認 识識 记記 议議 设設 计計 订訂 讨討 训訓 许許
论論 证證 评評 词詞 译譯 试試 话話 语語 读讀 变變 边邊 达達 迟遲 运運 远遠 连連 进進 选選 递遞
遗遺 适適 随隨 难難 双雙 观觀 欢歡 权權 报報 场場 块塊 坏壞 处處 备備 夺奪 奋奮 妈媽 师師 帮幫
带帶 庆慶 应應 张張 弹彈 归歸 当當 录錄 总總 恋戀 惊驚 愿願 战戰 户戶 扰擾 担擔 拥擁 挂掛 换換
损損 据據 择擇 敌敵 数數 断斷 暂暫 术術 杀殺 条條 极極 构構 标標 档檔 桥橋 检檢 楼樓 乐樂 欧歐
气氣 汉漢 汤湯 沟溝 济濟 热熱 爷爺 状狀 独獨 猫貓 电電 画畫 疗療 离離 种種 积積 称稱 笔筆 类類
给給 网網 罗羅 习習 联聯 聪聰 脑腦 艺藝 节節 药藥 营營 虽雖 规規 视視 览覽 觉覺 访訪 误誤 调調
费費 资資 赛賽 赶趕 跃躍 轻輕 辆輛 转轉 软軟 输輸 办辦 银銀 错錯 钱錢 铁鐵 闻聞 阳陽 阵陣 际際
页頁 题題 颜顏 风風 飞飛 饭飯 驾駕 鲜鮮 齐齊 龙龍 儿兒 兰蘭 关關 兴興 养養 剧劇 务務 劳勞 区區
医醫 华華 协協 单單 卫衛 厂廠 历歷 厅廳 参參 叹嘆 吨噸 响響 员員 圆圓 园園 围圍 图圖 圣聖 坚堅
坛壇 声聲 壳殼 够夠 夹夾 妇婦 娱娛 孙孫 宁寧 宝寶 审審 导導 层層 岁歲 岛島 币幣 广廣 庙廟 废廢
异異 弯彎 彻徹 径徑 忆憶 态態 怀懷 惯慣 愤憤 懒懶 戏戲 执執 扩擴 扫掃 护護 拟擬 挥揮 摇搖 显顯
晓曉 杂雜 毕畢 毁毀 汇匯 洁潔 浅淺 测測 浓濃 涛濤 渐漸 渊淵 温溫 湾灣 灭滅 灯燈 灵靈 灾災 炼煉
烦煩 烧燒 犹猶 狮獅 猎獵 献獻 环環 琐瑣 疯瘋 痴癡 盘盤 盖蓋 监監 睁睜 矿礦 码碼 础礎 确確 礼禮
祸禍 禅禪 稳穩 窍竅 竞競 签簽 简簡 粮糧 紧緊 纪紀 约約 级級 纯純 纱紗 纸紙 线線 组組 细細 织織
终終 绍紹 结結 绝絕 统統 继繼 续續 维維 综綜 缘緣 编編 缩縮 罚罰 职職 肤膚 肿腫 胜勝 胁脅 脚腳
舰艦 艰艱 获獲 虑慮 补補 袜襪 装裝 讲講 讯訊 讶訝 诉訴 诊診 诗詩 该該 详詳 诸諸 谈談 谊誼 谋謀
谎謊 谜謎 谨謹 贝貝 负負 贡貢 财財 责責 贤賢 败敗 货貨 质質 贩販 贪貪 购購 贯貫 贵貴 贷貸 贸貿
贺賀 赏賞 赐賜 赔賠 赖賴 赚賺 赞贊 赠贈 趋趨 踪蹤 轮輪 轰轟 迁遷 迈邁 邓鄧 邮郵 邻鄰 酿釀 释釋
针針 钓釣 钢鋼 钥鑰 钟鐘 钮鈕 铃鈴 铜銅 铭銘 锁鎖 锅鍋 锐銳 镇鎮 镜鏡 闭閉 闲閒 闹鬧 阅閱 队隊
阴陰 阶階 陈陳 陆陸 险險 隐隱 雾霧 静靜 韩韓 顶頂 顷頃 项項 顺順 须須 顾顧 顿頓 预預 领領 频頻
颗顆 额額 驱驅 验驗 骑騎 骗騙 鸡雞 鸭鴨 麦麥 黄黃 齿齒
""".split()

SIMPLIFIED_ONLY = frozenset(p[0] for p in _PAIRS)
TRADITIONAL_ONLY = frozenset(p[1] for p in _PAIRS)


def _is_han(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "\U00020000" <= ch <= "\U0002fa1f"


def empty_counts() -> dict:
    return {"traditional_lines": 0, "simplified_lines": 0, "mixed_lines": 0, "neutral_lines": 0,
            "traditional_chars": 0, "simplified_chars": 0, "neutral_chars": 0}


def count_scripts(text: str) -> dict:
    """Lines and Han characters by script. A line is Traditional or Simplified when
    it holds characters of only that script, mixed when it holds both, neutral when
    its Han characters are all shared by the two scripts. Lines with no Han character
    (blank, Latin, digits, punctuation) are not counted, nor are such characters."""
    counts = empty_counts()
    for line in (text or "").splitlines():
        trad = sum(ch in TRADITIONAL_ONLY for ch in line)
        simp = sum(ch in SIMPLIFIED_ONLY for ch in line)
        han = sum(_is_han(ch) for ch in line)
        if not han:
            continue
        counts["traditional_chars"] += trad
        counts["simplified_chars"] += simp
        counts["neutral_chars"] += han - trad - simp
        kind = "mixed" if trad and simp else "traditional" if trad else "simplified" if simp else "neutral"
        counts[f"{kind}_lines"] += 1
    return counts


def add_counts(total: dict, part: dict) -> dict:
    """Sum two count dicts (a run's total over its cases)."""
    return {k: total[k] + part[k] for k in total}
