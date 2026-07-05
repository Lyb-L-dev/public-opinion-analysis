import jieba
import jieba.analyse
from snownlp import SnowNLP
import re
from collections import Counter
import logging
import threading

# KeyBERT 延迟导入（避免模型加载失败导致整个模块不可用）
# KeyBERT 用于关键词提取，情感分析不依赖它
try:
    from keybert import KeyBERT
    _keybert_available = True
except ImportError:
    KeyBERT = None
    _keybert_available = False

logger = logging.getLogger(__name__)

# 初始化jieba
jieba.initialize()

# ---------------------- 优化：扩充情感词库（提升校准效果）----------------------
# 情感词库校准配置参数
SENTIMENT_CALIBRATION_CONFIG = {
    'base_weight': 0.08,       # 基础权重
    'length_decay': 0.01,      # 长度衰减系数
    'length_exp': 0.5,         # 长度指数
    'max_adjustment': 0.15     # 最大调整幅度
}

# 扩充后的正面情感词库（150+词汇，覆盖舆情多场景）
POSITIVE_WORDS = {
    # --- 基础评价词 (40+) ---
    "好", "棒", "优秀", "佳", "优", "出色", "完美", "卓越", "良好", "优质",
    "不错", "挺好", "真好", "超好", "太好", "蛮好", "尚好", "至好", "绝佳", "顶好",
    "点赞", "支持", "赞成", "认同", "认可", "赞同", "肯定", "表扬", "赞赏", "欣赏",
    "佩服", "敬佩", "致敬", "好评", "力荐", "推荐", "赞", "good", "nice", "great",

    # --- 情绪表达词 (50+) ---
    "开心", "高兴", "快乐", "愉快", "喜悦", "欣喜", "欢乐", "欢快", "狂喜", "欣慰",
    "满意", "满足", "如意", "如愿", "舒心", "安心", "放心", "暖心", "贴心", "感动",
    "激动", "振奋", "鼓舞", "兴奋", "热烈", "沸腾", "欢腾", "亢奋", "惊喜", "惊艳",
    "给力", "提气", "长脸", "争光", "争气", "扬眉吐气", "热血沸腾", "激情澎湃",
    "可爱", "呆萌", "漂亮", "美丽", "帅气", "靓", "酷", "炫", "妙", "妙啊",

    # --- 能力肯定词 (30+) ---
    "厉害", "强大", "强", "有力", "高效", "专业", "靠谱", "牛", "牛逼", "牛批",
    "神", "大神", "巨头", "顶级", "顶尖", "一流", "领先", "超前", "先进",
    "非凡", "不凡", "突出", "显著", "明显", "惊艳", "震撼", "炸裂", "逆天",

    # --- 正面预测词 (25+) ---
    "希望", "期待", "曙光", "光明", "好转", "改善", "进步", "发展", "成长", "成功",
    "胜利", "突破", "进展", "成就", "辉煌", "复兴", "崛起", "腾飞", "跃升",
    "上扬", "增长", "上升", "上涨", "增值", "升值", "盈利", "获利",

    # --- 舆情正向词 (30+) ---
    "回应", "澄清", "辟谣", "解释", "说明", "道歉", "致歉", "认错", "改正", "整改",
    "解决", "处理", "落实", "推进", "贯彻", "执行", "行动", "举措", "措施", "方案",
    "政策", "利好", "好消息", "喜讯", "捷报", "喜报", "功绩", "贡献",
    "正义", "公平", "公正", "公开", "透明", "和谐", "富强", "文明"
}

# 扩充后的负面情感词库（150+词汇，覆盖舆情多场景）
NEGATIVE_WORDS = {
    # --- 基础差评词 (40+) ---
    "差", "烂", "糟糕", "垃圾", "废物", "渣", "没用", "差劲", "恶劣", "低劣", "拙劣",
    "劣质", "粗糙", "劣等", "低质", "残次", "失望", "绝望", "失落", "失意", "沮丧",
    "颓废", "丧气", "泄气", "气馁", "悲观", "负面", "消极", "阴暗", "灰暗", "暗淡",
    "凄凉", "悲哀", "悲伤", "可怜", "可悲", "可叹", "可惜", "遗憾", "惋惜",

    # --- 强烈负面词 (45+) ---
    "荒谬", "离谱", "荒诞", "可笑", "滑稽", "讽刺", "挖苦", "嘲弄", "嘲讽", "恶心",
    "厌恶", "讨厌", "厌烦", "厌倦", "憎恶", "憎恨", "痛恨", "痛斥", "谴责", "气愤",
    "愤怒", "恼火", "生气", "发怒", "大怒", "暴怒", "狂怒", "盛怒", "不满", "抱怨",
    "埋怨", "吐槽", "牢骚", "怨恨", "嫉恨", "仇视", "敌视", "傻", "蠢", "智障",

    # --- 危机相关词 (35+) ---
    "危险", "可怕", "恐怖", "惊恐", "惊慌", "恐慌", "恐惧", "害怕", "担心", "担忧",
    "焦虑", "忧虑", "忧心", "不安", "慌乱", "紧急", "紧迫", "危急", "严重", "危机",
    "风险", "隐患", "威胁", "危害", "损害", "破坏", "损伤", "伤害", "死亡", "伤亡",
    "牺牲", "遇难", "身亡", "惨死", "遇害", "受灾", "灾难", "悲剧", "末日",

    # --- 负面预测词 (20+) ---
    "失败", "倒闭", "崩溃", "瓦解", "破灭", "落空", "失误", "过错", "错误", "罪过",
    "亏损", "赔钱", "负债", "债务", "衰退", "萎缩", "下滑", "下跌", "贬值", "降级",

    # --- 舆情负向词 (35+) ---
    "隐瞒", "掩盖", "遮掩", "掩饰", "伪装", "欺骗", "欺诈", "诈骗", "诱骗", "坑骗",
    "虚假", "造假", "捏造", "伪造", "诬陷", "诬告", "陷害", "冤枉", "委屈", "推诿",
    "敷衍", "搪塞", "塞责", "渎职", "失职", "贪污", "腐败", "受贿", "行贿",
    "黑幕", "黑心", "丑闻", "曝光", "泄露", "内幕", "潜规则", "暗箱操作"
}

# ---------------------- 新增：中文停用词库（过滤无意义词汇，提升关键词准确率）----------------------
# 基础中文停用词（覆盖常见无意义词汇，避免关键词提取出现"的、地、得"等）
# 以及热搜中常见的无意义词汇（如"男子"、"女子"、"网友"等）
BASE_STOPWORDS = {
    "的", "地", "得", "了", "着", "过", "是", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "她们", "它们", "这", "那", "此", "彼", "之",
    "和", "与", "或", "但", "却", "而", "于", "在", "从", "到", "对",
    "对于", "关于", "把", "被", "将", "让", "使", "能", "会", "可以",
    "要", "应", "该", "可", "也", "还", "又", "才", "就", "都",
    "只", "仅", "刚", "正", "已", "未", "不", "没", "无", "非",
    "哦", "啊", "呀", "呢", "吧", "吗", "嘛", "哼", "哈", "嘿",
    # 热搜常见无意义词汇
    "男子", "女子", "女生", "网友", "事件", "回应", "最新", "曝光", "公开",
    "消息", "发布", "表示", "称", "称其", "本人", "一方", "双方",
    "其中", "之后", "之前", "目前", "现在", "今日", "昨日", "明日",
    "通报", "公告", "说明", "回应", "致歉", "道歉", "声明", "情况",
    "问题", "原因", "结果", "内容", "视频", "图片", "官方", "证实"
}

# 全局停用词集合（支持外部扩展）
global_stopwords = set(BASE_STOPWORDS)

# ---------------------- 新增：默认事件分类模板（舆情场景常见分类，用户可直接使用）----------------------
# 分类优先级：按热搜中出现频率排列，社会民生/政治时事优先
DEFAULT_EVENT_CATEGORIES = {
    "社会民生": (
        # --- 教育考试（热搜最高频，优先匹配） ---
        r"考研国家线|考研出分|教资考试|教资作文|教资|国家线发布|高考报名|高考|中考|考公|考编|考博|艺考|公务员考试|阅卷|录取|分数线|投档|考公|考研|"
        # --- 劳动就业 ---
        r"就业|入职|失业|裁员|降薪|工资|社保|加班|劳动法|劳动仲裁|工伤|退休|养老金|请假|怀孕|生育|职场|"
        # --- 社会安全/事故（不含爆炸，爆炸归政治时事） ---
        r"车祸|火灾|溺水|坠楼|地震|洪水|台风|暴雨|干旱|泥石流|滑坡|矿难|踩踏|"
        r"事故|案件|犯罪|诈骗|盗窃|抢劫|杀人|强奸|自杀|家暴|虐待|拐卖|走私|网暴|"
        # --- 消费者/维权 ---
        r"退款|退货|投诉|维权|举报|曝光|欺诈|虚假宣传|假冒伪劣|霸王条款|获赔|赔偿|砍一刀|"
        # --- 日常民生 ---
        r"物业|业主|停车|垃圾分类|环保|污染|雾霾|停电|停水|限行|限购|摇号|公租房|保障房|"
        # --- 人物泛指 ---
        r"女子|女生|男生|老人|儿童|孩子|婴儿|乘客|旅客|市民|居民|百姓|"
        # --- 其他社会类 ---
        r"社会|民生|生活|热搜|紧急|突发|通报|课间|全省份|椰树"
    ),
    "政治时事": (
        # --- 核心政治词汇（避免过宽词如'国家'、'中'导致误匹配）---
        r"政治|时事|政策|政府|会议|法案|选举|领导人|总统|总理|首相|主席|总书记|部长|大使|领事|国防部长|外长|发言人|人大常委会|人大代表|委员|"
        # --- 国际组织 ---
        r"联合国|北约|WTO|IMF|世界银行|G20|APEC|欧盟|东盟|上合组织|OPEC|"
        # --- 军事/战争相关（热搜最高频） ---
        r"军事|军队|演习|武器|导弹|军舰|战机|坦克|核武器|核弹|航母|潜艇|轰炸|空袭|打击|防御|军事行动|击沉|爆炸|"
        # --- 地区/国际冲突 ---
        r"伊朗|以色列|德黑兰|德里兰|霍尔木兹|哈梅内伊|内贾德|委内瑞拉|美军|以军|伊军|美伊|中伊|以伊|美以|冲突|战争|开火|入侵|反击|报复|"
        # --- 领土/主权 ---
        r"香港|台湾|西藏|新疆|南海|钓鱼岛|领土|主权|统一|独立|分裂|收复|军事基地|制裁|禁运|谈判|协议|条约|声明|抗议|"
        # --- 中国政治 ---
        r"反腐败|廉政|扫黑|除恶|政法|法院|检察院|公安|监察|巡视|督查|十四届|全国人大|国务院|央行|人民银行|公告|"
        # --- 其他国家/领袖 ---
        r"日本|俄罗斯|普京|特朗普|拜登|英国|德国|法国|澳大利亚|韩国|朝鲜|菲律宾|印度|巴西|以色列|伊朗|白宫"
    ),
    "财经金融": (
        # --- 市场/行情 ---
        r"财经|金融|股票|基金|理财|银行|保险|证券|汇率|房价|物价|经济|投资|融资|并购|重组|"
        r"A股|港股|美股|上证|深证|创业板|科创板|纳斯达克|道琼斯|恒生指数|指数|大盘|板块|涨跌|"
        r"涨停|跌停|停牌|退市|上市|IPO|增发|配股|财报|业绩|营收|利润|亏损|破产|分红|股息|"
        # --- 大宗商品 ---
        r"黄金|金价|原油|油价|石油|期货|大宗商品|煤炭|天然气|铜|铝|钢材|中石油|中石化|油阀|"
        # --- 外汇 ---
        r"人民币|美元|欧元|日元|英镑|外汇|汇率|升值|贬值|"
        # --- 理财 ---
        r"理财|国债|债券|基金净值|收益率|年化|本金|利息|"
        # --- 出行相关 ---
        r"机票|车票|火车票|12306|候补|机票|船票|高速|收费|油价|"
        # --- 经济数据 ---
        r"GDP|PMI|CPI|PPI|就业率|消费|出口|进口|贸易顺差|逆差|关税|通胀|通缩|"
        # --- 公司 ---
        r"公司|企业|商家|商户|平台|订单|退款|投诉|客服|加油站|中石油|中石化"
    ),
    "娱乐明星": (
        # --- 核心娱乐词 ---
        r"娱乐|明星|演员|歌手|电影|电视剧|综艺|演唱会|专辑|红毯|颁奖|颁奖礼|颁奖典礼|"
        r"制片|导演|编剧|主持人|经纪人|剧透|开机|杀青|定档|撤档|路透|"
        # --- 流量/粉丝 ---
        r"偶像|选秀|出道|签约|解约|塌房|粉丝|应援|打榜|控评|超话|CP|站姐|私生饭|"
        # --- 情感八卦 ---
        r"八卦|恋情|分手|复合|结婚|离婚|出轨|劈腿|小三|人设|人设崩塌|曝光|辟谣|否认|"
        r"世纪复合|世纪婚礼|世纪分手|官宣|领证|怀孕|生子|生育|"
        # --- 票房收视 ---
        r"票房|收视率|收视率|豆瓣|评分|口碑|差评|好评|难看|精彩|"
        # --- 综艺 ---
        r"真人秀|脱口秀|慢综艺|竞技|淘汰|晋级|中戏|北影|上戏|艺术院校|传媒大学|"
        # --- 名人 ---
        r"明星|网红|主播|达人|博主|Vlog|短视频|直播带货"
    ),
    "科技互联网": (
        # --- 科技 ---
        r"科技|技术|创新|突破|研发|发布|首发|量产|交付|"
        # --- 互联网公司 ---
        r"苹果|华为|小米|OPPO|VIVO|三星|英特尔|NVIDIA|AMD|高通|谷歌|微软|OpenAI|"
        r"腾讯|阿里|字节|百度|京东|美团|滴滴|拼多多|网易|新浪|微博|哔哩|抖音|快手|小红书|"
        # --- 新兴技术 ---
        r"AI|人工智能|大数据|云计算|区块链|元宇宙|ChatGPT|GPT|文心|通义|KIMI|"
        r"Sora|DeepSeek|通义千问|Kimi|豆包|R1|"
        # --- 硬件数码 ---
        r"数码|手机|电脑|平板|笔记本|耳机|音箱|手表|手环|相机|镜头|屏幕|芯片|固态电池|"
        # --- 软件/平台 ---
        r"软件|系统|漏洞|病毒|勒索|数据泄露|隐私|服务器|网站|APP|小程序|版本|更新|上线|"
        # --- 新能源 ---
        r"芯片|半导体|新能源|电动汽车|特斯拉|比亚迪|宁德|小米汽车|固态电池|自动驾驶|智驾|无人驾驶|"
        # --- 通信 ---
        r"5G|6G|4G|流量|运营商|套餐|携号转网"
    ),
    "体育赛事": (
        # --- 运动项目 ---
        r"体育|赛事|奥运|世界杯|篮球|足球|排球|乒乓球|羽毛球|网球|田径|游泳|跳水|体操|举重|拳击|格斗|赛车|F1|高尔夫|冰球|棒球|垒球|斯诺克|电竞|马拉松|"
        # --- 队伍/人员 ---
        r"球队|俱乐部|国家队|运动员|教练|裁判|进球|得分|夺冠|冠军|亚军|季军|MVP|金靴|金球|"
        r"球员|门将|前锋|中场|后卫|主帅|助教|解说|嘉宾|"
        # --- 赛事 ---
        r"CBA|NBA|欧冠|英超|西甲|意甲|德甲|中超|中甲|世预赛|亚洲杯|联合会杯|美洲杯|欧洲杯|东亚杯|亚运会|全运会|锦标赛|公开赛|大奖赛|邀请赛|"
        # --- 奥运 ---
        r"圣火|火炬|开幕式|闭幕式|颁奖|领奖台|奖牌|金牌|银牌|铜牌|破纪录|世界纪录|奥运村|残奥|"
        # --- 规则/判罚 ---
        r"点球|任意球|黄牌|红牌|加时赛|点球大战|绝杀|逆转|越位|换人|暂停|退赛|弃权|兴奋剂|黑哨|假球"
    ),
    "文化旅游": (
        # --- 文化 ---
        r"文化|传统|艺术|文学|书籍|绘画|书法|摄影|展览|博物馆|图书馆|剧院|文物保护|非遗|非遗传承|国潮|汉服|古装|国风|考古|遗址|文物修复|妈祖|"
        # --- 旅行目的地 ---
        r"旅行|旅游|景区|景点|古镇|古城|寺庙|教堂|宫殿|长城|故宫|西湖|黄山|泰山|张家界|九寨沟|丽江|大理|三亚|厦门|"
        r"北京|上海|西安|成都|重庆|杭州|南京|苏州|武汉|长沙|青岛|哈尔滨|长春|沈阳|郑州|济南|天津|香港|澳门|台湾|西藏|新疆|"
        r"迪拜|马尔代夫|巴黎|纽约|东京|首尔|曼谷|新加坡|悉尼|伦敦|罗马|巴厘岛|普吉岛|"
        # --- 节假日 ---
        r"节假日|春节|清明|端午|中秋|国庆|元旦|五一|暑假|寒假|黄金周|旅游季|母亲节|父亲节|情人节|圣诞节|万圣节|重阳节|元宵节|建军节|妇女节|儿童节|教师节|护士节|"
        # --- 美食 ---
        r"美食|餐厅|小吃|特产|夜市|网红餐厅|米其林|"
        # --- 交通出行 ---
        r"机票|酒店|民宿|签证|护照|出入境|海关|机场|火车站|高铁|大巴"
    ),
    "健康养生": (
        # --- 医疗 ---
        r"健康|养生|医疗|医院|医生|护士|挂号|门诊|住院|手术|治疗|康复|保健|体检|看病|处方|急诊|专家|名医|专家号|"
        r"医保|医疗险|保险报销|就医|转院|出院|病床|ICU|手术费|治疗费|"
        # --- 疾病 ---
        r"病毒|细菌|感染|传染|流感|新冠|甲流|乙肝|艾滋病|癌症|肿瘤|心血管|糖尿病|高血压|心脏病|脑卒中|中风|肺炎|支气管炎|哮喘|过敏|皮肤病|眼科|牙科|精神科|心理科|"
        # --- 疫苗/药品 ---
        r"疫苗|接种|核酸|隔离|防控|特效药|进口药|仿制药|中药|西药|处方药|非处方|服药|吃药|打针|输液|"
        # --- 防护/症状 ---
        r"发烧|咳嗽|感冒|腹泻|呕吐|头痛|失眠|抑郁|焦虑|白发|脱发|近视|老花|"
        # --- 生活方式 ---
        r"饮食|运动|睡眠|心理|减肥|健身|瑜伽|跑步|马拉松|免疫力|排毒|养生|食补|药补|保健品|营养|维生素|蛋白质|膳食|补铁|补钙|补锌|"
        # --- 美容 ---
        r"美容|整形|护肤|化妆品|整形|祛斑|美白|抗衰|微整|医美|护肤|防晒"
    )
}

def analyze_sentiment(text, calibration_config=None):
    """
    分析文本情感（增强版：扩充情感词库+优化波动校准+文本长度裁剪）
    保留原有核心逻辑，提升准确率和稳定性

    Args:
        text: 待分析文本
        calibration_config: 校准配置字典，默认使用 SENTIMENT_CALIBRATION_CONFIG
    """
    if calibration_config is None:
        calibration_config = SENTIMENT_CALIBRATION_CONFIG

    try:
        # 1. 先预处理文本（去除特殊字符、空字符）
        clean_text = preprocess_text(text)
        # 2. 校验空文本/无效文本
        if not clean_text or len(clean_text.strip()) == 0:
            return 0.5
        # 3. 新增：文本长度裁剪（避免过长文本影响分析效率，保留核心情感信息）
        clean_text = truncate_text(clean_text, max_length=500)
        # 4. 分词校验（避免SnowNLP计算时除以零）
        words = jieba.lcut(clean_text)
        valid_words = [w for w in words if len(w.strip()) > 0 and not w.isspace() and w not in global_stopwords]
        if len(valid_words) == 0:
            return 0.5
        # 5. 执行SnowNLP情感分析（核心保留）
        s = SnowNLP(clean_text)
        sentiment_score = s.sentiments
        # 6. 优化：情感词校准（使用配置的参数）
        pos_count = sum(1 for w in valid_words if w in POSITIVE_WORDS)
        neg_count = sum(1 for w in valid_words if w in NEGATIVE_WORDS)
        # 校准权重因子（使用配置的参数）
        weight_factor = (
            calibration_config['base_weight'] /
            (1 + calibration_config['length_decay'] * len(valid_words) ** calibration_config['length_exp'])
        )
        weight_factor = min(weight_factor, calibration_config['max_adjustment'])

        if pos_count > neg_count:
            # 正面词更多，轻微拉高分数（不超过1.0）
            sentiment_score = min(1.0, sentiment_score + weight_factor * pos_count)
        elif neg_count > pos_count:
            # 负面词更多，轻微拉低分数（不低于0.0）
            sentiment_score = max(0.0, sentiment_score - weight_factor * neg_count)

        # 7. 边界值校验+保留3位小数（匹配数据库decimal(4,3)格式）
        return max(0.0, min(1.0, round(sentiment_score, 3)))

    # 专门捕获除以零错误（核心修复）
    except ZeroDivisionError:
        logger.warning(f"情感分析错误：文本[{text[:20] if text else '空'}...]触发除以零，返回中性分")
        return 0.5
    except Exception as e:
        logger.error(f"情感分析错误: {e}，文本[{text[:20] if text else '空'}...]")
        return 0.5


def batch_analyze_sentiment(texts, batch_size=100):
    """
    批量情感分析（优化处理效率）

    Args:
        texts: 文本列表
        batch_size: 每批处理数量，默认100

    Returns:
        情感分数列表，与输入文本顺序对应
    """
    if not texts:
        return []

    results = []
    total = len(texts)

    for i in range(0, total, batch_size):
        batch = texts[i:i + batch_size]
        batch_results = []

        for text in batch:
            try:
                score = analyze_sentiment(text)
                batch_results.append(score)
            except Exception as e:
                logger.warning(f"批量情感分析单项失败: {e}")
                batch_results.append(0.5)  # 失败时返回中性分

        results.extend(batch_results)

        # 每处理完一个批次输出日志
        if (i + batch_size) % 500 == 0 or (i + batch_size) >= total:
            logger.info(f"批量情感分析进度: {min(i+batch_size, total)}/{total}")

    return results


def _analyze_single_text(text):
    """单条文本情感分析（用于多进程）"""
    try:
        return analyze_sentiment(text)
    except Exception as e:
        return 0.5


def batch_analyze_sentiment_parallel(texts, batch_size=500, num_workers=None):
    """
    批量情感分析（多进程并行版本，大幅提升处理速度）

    Args:
        texts: 文本列表
        batch_size: 每批处理数量，默认500
        num_workers: 并行进程数，默认CPU核心数

    Returns:
        情感分数列表，与输入文本顺序对应
    """
    if not texts:
        return []

    try:
        from multiprocessing import Pool, cpu_count
    except ImportError:
        logger.warning("多进程模块不可用，回退到串行处理")
        return batch_analyze_sentiment(texts, batch_size)

    if num_workers is None:
        num_workers = max(1, cpu_count() - 1)

    total = len(texts)
    logger.info(f"启动多进程情感分析: {total}条数据, {num_workers}个进程")

    # 将数据分成多个批次
    batches = [texts[i:i + batch_size] for i in range(0, total, batch_size)]

    try:
        with Pool(processes=num_workers) as pool:
            batch_results = pool.map(_analyze_single_text, texts)

        results = list(batch_results)
        logger.info(f"多进程情感分析完成: {len(results)}条数据")
        return results
    except Exception as e:
        logger.error(f"多进程情感分析失败，回退到串行处理: {e}")
        return batch_analyze_sentiment(texts, batch_size)


def get_sentiment_type(score):
    """根据情感分数返回情感类型（保留原有逻辑，无改动）"""
    try:
        score = float(score)
        if score < 0.3:
            return "负面"
        elif score > 0.7:
            return "正面"
        else:
            return "中性"
    except (ValueError, TypeError):
        logger.warning(f"情感类型判断失败：无效分数[{score}]，返回中性")
        return "中性"


def extract_keywords(texts, top_k=20, with_stopwords_filter=True, with_weight=True):
    """
    提取关键词（增强版：过滤停用词+去重+单/多文本兼容+优化返回格式）
    :param texts: 文本列表/单文本
    :param top_k: 返回前k个关键词
    :param with_stopwords_filter: 是否过滤停用词（默认True）
    :param with_weight: 是否返回权重（默认True，兼容原有逻辑）
    :return: 关键词列表（带权重/仅关键词）
    """
    # 兼容单文本输入（用户传入单个字符串而非列表）
    if isinstance(texts, str):
        texts = [texts]
    if not texts:
        return []

    # 1. 文本预处理+去重（避免重复文本拉高关键词权重）
    processed_texts = [preprocess_text(t) for t in texts if t]
    unique_texts = remove_duplicate_texts(processed_texts)
    all_text = ' '.join(unique_texts)

    # 2. 校验处理后的文本是否为空
    if not all_text.strip():
        logger.warning("关键词提取：处理后的文本为空，返回空列表")
        return []

    try:
        # 3. 结巴关键词提取（保留原有核心逻辑）
        keywords = jieba.analyse.extract_tags(
            all_text,
            topK=top_k,
            withWeight=True,
            allowPOS=('n', 'vn', 'v', 'ns', 'nr')
        )

        # 4. 新增：过滤停用词（提升关键词有效性）
        if with_stopwords_filter:
            keywords = [(word, weight) for word, weight in keywords if word not in global_stopwords]

        # 5. 新增：关键词去重（避免重复词汇出现）
        keyword_dict = {}
        for word, weight in keywords:
            if word not in keyword_dict or weight > keyword_dict[word]:
                keyword_dict[word] = weight
        keywords = sorted(keyword_dict.items(), key=lambda x: x[1], reverse=True)[:top_k]

        # 6. 兼容返回格式（带权重/仅关键词）
        if with_weight:
            return keywords
        else:
            return [word for word, _ in keywords]

    except Exception as e:
        logger.error(f"关键词提取错误: {e}")
        return []


def categorize_event(title, categories_patterns=None):
    """
    对事件进行分类（增强版：提供默认分类模板+优化正则匹配+避免分类重叠）
    :param title: 事件标题
    :param categories_patterns: 自定义分类正则字典（默认使用内置舆情分类模板）
    :return: 事件分类
    """
    # 1. 使用默认分类模板（用户未传入时）
    if not categories_patterns or not isinstance(categories_patterns, dict):
        categories_patterns = DEFAULT_EVENT_CATEGORIES

    # 2. 校验标题有效性
    if not title:
        return '其他'

    title = str(title).strip()
    # 预处理标题（去除特殊字符，提升匹配准确率）
    clean_title = preprocess_text(title)

    # 3. 正则匹配分类（按预设顺序，避免重叠分类优先匹配）
    for category, pattern in categories_patterns.items():
        try:
            # 支持 pattern 为字符串或元组（元组时用 | 连接成联合正则）
            if isinstance(pattern, tuple):
                combined_pattern = '|'.join(pattern)
            else:
                combined_pattern = pattern
            if re.search(combined_pattern, clean_title, re.IGNORECASE):
                return category
        except re.error as e:
            logger.warning(f"分类正则匹配错误：{e}，分类[{category}]，跳过该分类")
            continue

    return '其他'


def preprocess_text(text):
    """
    预处理文本（增强版：新增舆情场景无效内容过滤+保留更多情感相关字符）
    保留情感相关标点，避免过度清洗影响SnowNLP结果，同时去除无意义内容
    """
    if not text:
        return ""

    text_str = str(text)
    # 1. 去除舆情场景常见无效内容
    text_str = re.sub(r'https?://[^\s]+', '', text_str)  # 去除网址
    text_str = re.sub(r'@[^\s]+', '', text_str)  # 去除@用户（如@小明）
    text_str = re.sub(r'#([^\s]+)#', r'\1', text_str)  # 去除话题标签#xxx#，保留话题内容
    text_str = re.sub(r'[^\w\u4e00-\u9fff\s，。！？；：""''（）()【】、·]', '', text_str)  # 保留核心字符
    text_str = re.sub(r'[^\w\u4e00-\u9fff\s，。！？]', '', text_str)  # 进一步精简，保留关键情感标点

    # 2. 去除多余空格和换行符
    text_str = re.sub(r'[\n\r\t]+', ' ', text_str)
    text_str = re.sub(r'\s+', ' ', text_str)

    # 3. 去除首尾空格
    return text_str.strip()

# ---------------------- 新增：实用工具方法（提升模块复用性，贴合舆情场景）----------------------

# [预留功能] 以下函数暂未在当前系统中使用，保留用于未来功能扩展
# - load_stopwords(): 支持从外部文件加载自定义停用词库
# - append_sentiment_words(): 支持扩展自定义情感词库

def load_stopwords(stopwords_file: object = None, append: object = True) -> bool:
    """
    [预留功能] 加载外部停用词库（支持txt文件，一行一个停用词）
    :param stopwords_file: 停用词文件路径
    :param append: 是否追加到现有停用词（True）或覆盖（False）
    :return: 加载是否成功
    """
    global global_stopwords
    if not stopwords_file:
        logger.warning("加载停用词失败：文件路径为空")
        return False

    try:
        with open(stopwords_file, 'r', encoding='utf-8') as f:
            new_stopwords = set([line.strip() for line in f if line.strip()])

        if append:
            global_stopwords.update(new_stopwords)
        else:
            global_stopwords = new_stopwords

        logger.info(f"停用词库加载成功，当前停用词总数：{len(global_stopwords)}")
        return True
    except Exception as e:
        logger.error(f"加载停用词失败：{e}，文件路径[{stopwords_file}]")
        return False

def append_sentiment_words(positive_words=None, negative_words=None):
    """
    [预留功能] 追加情感词库（支持用户自定义扩展，提升情感分析准确率）
    :param positive_words: 正面情感词列表
    :param negative_words: 负面情感词列表
    :return: 追加是否成功
    """
    global POSITIVE_WORDS, NEGATIVE_WORDS
    try:
        if positive_words and isinstance(positive_words, (list, set)):
            POSITIVE_WORDS.update(set(positive_words))
        if negative_words and isinstance(negative_words, (list, set)):
            NEGATIVE_WORDS.update(set(negative_words))

        logger.info(f"情感词库追加成功，当前正面词数：{len(POSITIVE_WORDS)}，负面词数：{len(NEGATIVE_WORDS)}")
        return True
    except Exception as e:
        logger.error(f"追加情感词库失败：{e}")
        return False

def cut_text(text, with_stopwords_filter=True):
    """
    文本分词（独立方法，供上层模块复用，避免重复写jieba逻辑）
    :param text: 待分词文本
    :param with_stopwords_filter: 是否过滤停用词
    :return: 分词结果列表
    """
    if not text:
        return []

    clean_text = preprocess_text(text)
    words = jieba.lcut(clean_text)

    if with_stopwords_filter:
        return [word for word in words if word not in global_stopwords and word.strip()]
    else:
        return [word for word in words if word.strip()]

def truncate_text(text, max_length=500):
    """
    文本长度裁剪（避免过长文本影响分析效率，保留核心内容）
    :param text: 待裁剪文本
    :param max_length: 最大长度限制
    :return: 裁剪后的文本
    """
    if not text:
        return ""

    text_str = str(text).strip()
    if len(text_str) <= max_length:
        return text_str

    # 保留前max_length个字符（尽量保留开头核心信息，贴合舆情分析）
    return text_str[:max_length] + "..."

def remove_duplicate_texts(texts):
    """
    文本列表去重（避免重复文本拉高关键词权重，提升分析准确率）
    :param texts: 文本列表
    :return: 去重后的文本列表
    """
    if not isinstance(texts, list):
        return []

    # 去重同时保留原有顺序（使用有序字典）
    unique_texts = list(dict.fromkeys([t for t in texts if t]))
    return unique_texts



# ========================= 新增：KeyBERT 关键词提取（深度学习版） =========================

def extract_keywords_bert(texts, top_k=20, with_weight=True):
    """
    使用 TF-IDF 提取关键词（已跳过 KeyBERT，直接使用本地 TF-IDF）
    :param texts: 文本列表
    :param top_k: 返回关键词数量
    :param with_weight: 是否返回权重
    :return: 关键词列表 [(word, weight), ...] 或 [word, ...]
    """
    if not texts:
        return []

    # 预处理文本（去重、清洗）
    processed_texts = [preprocess_text(t) for t in texts if t]
    unique_texts = remove_duplicate_texts(processed_texts)
    all_text = ' '.join(unique_texts)

    if not all_text.strip():
        logger.warning("关键词提取：处理后的文本为空")
        return []

    # 直接使用 TF-IDF 提取关键词
    logger.info("使用 TF-IDF 提取关键词...")
    try:
        keywords = jieba.analyse.extract_tags(
            all_text,
            topK=top_k,
            withWeight=True,
            allowPOS=('n', 'vn', 'v', 'ns', 'nr')
        )

        # 过滤停用词
        filtered = [(word, weight) for word, weight in keywords if word not in global_stopwords]

        # 去重
        keyword_dict = {}
        for word, weight in filtered:
            if word not in keyword_dict or weight > keyword_dict[word]:
                keyword_dict[word] = weight
        result = sorted(keyword_dict.items(), key=lambda x: x[1], reverse=True)[:top_k]

        if with_weight:
            return result
        else:
            return [word for word, _ in result]
    except Exception as e:
        logger.error(f"TF-IDF 关键词提取错误: {e}")
        return []