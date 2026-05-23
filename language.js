/**
 * CycleSense 前端语言引擎
 * 管理所有面向用户的动态文案
 * 风格参考：《CycleSense_前端语言设计.md》
 * 
 * 五条铁律：
 * 1. 永远从"你本来就很好"出发
 * 2. 不贴标签、不做笃定判断
 * 3. 不替用户做决定（她精力低也要上班）
 * 4. 正向导向，不先抑后扬
 * 5. 克制，一句即收
 */

const LANGUAGE = {

  // ━━━ 今日一句（状态页圆环下方）━━━
  todaySentence: {
    high: [
      "状态在，不需要等更好的时机。",
      "头脑清晰。手边的事可以放心展开。",
      "精力是有的。用在哪里，你说了算。",
      "你想做的那件事——今天是好时候。",
      "此刻，你的身体是可以信赖的。",
      "精力充盈的日子，像打开窗户时涌进来的那阵风。"
    ],
    medium: [
      "你一直知道怎么安排自己。今天也是。",
      "清醒、平静。适合处理那些需要耐心的事。",
      "你的时间表你做主。身体跟得上。",
      "中等偏稳。该做的事照常做，会做得不错。",
      "今天像一条平静的河。你在河上，方向你定。",
      "状态平稳。你比自己以为的更从容。"
    ],
    low: [
      "把最重要的一件事放在精力最好的那个小时里。其余的，常规推就好。",
      "此刻的你适合做已经想清楚的事。新决策可以放到明天。",
      "今天适合做'动手不动脑'的那类事。",
      "你知道今天的分寸在哪里。相信那个分寸。",
      "身体传来一种微微的沉。不影响做事，但值得被你注意到。",
      "你还在运转。只是今天的带宽窄了一些。"
    ],
    veryLow: [
      "你还在这里，还在做。这本身需要被看到。",
      "最省力的方式做完手头的事。今天的标准可以只是'做了'。",
      "能推的事就推。不能推的事，用最小力气过关就好。",
      "感谢你的心，它持续跳动，并未偷懒。你也是。",
      "身体在用储备金运转。每件事之间给自己多留十秒钟。",
      "今天不评判自己做得好不好。做了，就是全部。"
    ]
  },

  // ━━━ 策略大标题（状态页右侧）━━━
  strategyLine: {
    high: [
      "今天可以推进重要的事，方向你定。",
      "头脑清晰的一天。想展开什么都可以。",
      "状态好的时候不用省着用。"
    ],
    medium: [
      "稳步推进就好。你一直知道怎么安排。",
      "按你的节奏来。身体跟得上。",
      "继续就好。不急不缓。"
    ],
    low: [
      "把最重要的一件事，放在精力最好的时段。",
      "今天适合做确定的事。新的可以等一等。",
      "带宽窄了一些。该做的事排个轻重就好。"
    ],
    veryLow: [
      "今天用最小力气过关就好。",
      "做完眼前这一件。其余的，它们可以等。",
      "能推的推。不能推的，简单做完就好。"
    ]
  },

  // ━━━ 策略解释（策略大标题下方）━━━
  strategyCopy: {
    high: [
      "选一两件真正重要的事，放心展开。",
      "重要的沟通、决策，都可以安排在今天。"
    ],
    medium: [
      "按已有计划推进就好，不必额外加码。",
      "耐心活、收尾活，今天最合适。"
    ],
    low: [
      "流程性工作优先。复杂决策可以缓一缓。",
      "一件一件来，中间留一点空隙给自己。"
    ],
    veryLow: [
      "只做不能再推的事。标准放到最低也没关系。",
      "如果脑子里有雾感——写下来比记着省力。"
    ]
  },

  // ━━━ 每日回顾一句话（趋势页）━━━
  dayLine: {
    high: ["状态在，不用等更好的时机。", "可以展开一件在意的事。", "头脑清晰的一天。"],
    medium: ["节奏是稳的。", "适合收拢手边的事。", "按计划推就好。"],
    low: ["先做已经想清楚的事。", "选最重要的一件就好。", "带宽窄了，排个序。"],
    veryLow: ["今天的标准可以轻一点。", "做了就是全部。", "最小力气过关。"]
  },

  // ━━━ 围绝经期用户专用 ━━━
  perimenopause: {
    todaySentence: [
      "今天的带宽窄了一些。该做的事排个序就好。",
      "如果脑子里有雾感——写下来比记在脑子里省力。",
      "重要对话如果能选时间，选你相对清醒的那个窗口。",
      "身体传来一种微微的沉。不影响做事，但值得被你注意到。",
      "余量是暂时的资源分配——不是对你的评判。"
    ],
    strategyLine: [
      "把最重要的事，放在相对清醒的时段。",
      "该做的事排个轻重。你知道怎么分配。",
      "今天适合做确定的事。"
    ]
  }
};

// ━━━ 核心函数 ━━━

function getEnergyTier(energy) {
  if (energy >= 75) return 'high';
  if (energy >= 50) return 'medium';
  if (energy >= 30) return 'low';
  return 'veryLow';
}

// 用日期做seed，同一天同一精力档返回同一句（刷新不变）
function pickByDate(arr, dateStr) {
  if (!arr || arr.length === 0) return '';
  const hash = dateStr ? dateStr.split('').reduce((a, c) => a + c.charCodeAt(0), 0) : new Date().getDate();
  return arr[hash % arr.length];
}

/**
 * 获取今日语言包
 * @param {number} energy - 精力值 0-100
 * @param {string} userType - 用户类型 "regular"/"pcos"/"perimenopause"
 * @returns {object} { sentence, strategyLine, strategyCopy }
 */
function getTodayLanguage(energy, userType) {
  const tier = getEnergyTier(energy);
  const today = new Date().toISOString().slice(0, 10);

  // 围绝经期用户使用专用句库
  if (userType === 'perimenopause') {
    return {
      sentence: pickByDate(LANGUAGE.perimenopause.todaySentence, today),
      strategyLine: pickByDate(LANGUAGE.perimenopause.strategyLine, today),
      strategyCopy: pickByDate(LANGUAGE.strategyCopy[tier], today)
    };
  }

  return {
    sentence: pickByDate(LANGUAGE.todaySentence[tier], today),
    strategyLine: pickByDate(LANGUAGE.strategyLine[tier], today),
    strategyCopy: pickByDate(LANGUAGE.strategyCopy[tier], today)
  };
}

/**
 * 获取某天的回顾一句话
 * @param {number} energy - 当天精力值
 * @param {string} dateStr - 日期字符串如"5/23"
 */
function getDayLine(energy, dateStr) {
  const tier = getEnergyTier(energy);
  return pickByDate(LANGUAGE.dayLine[tier], dateStr);
}
