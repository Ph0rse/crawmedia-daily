#!/usr/bin/env node

/**
 * 抖音热榜抓取脚本（v2.0.0）
 * 获取抖音热搜榜数据，支持关键词过滤、JSON 机器可读输出
 *
 * 用法：
 *   node scripts/douyin.js hot [数量] [--filter 关键词1,关键词2] [--json]
 *
 * 示例：
 *   node scripts/douyin.js hot 20 --filter 科技,体育
 *   node scripts/douyin.js hot --json              # JSON 输出（供程序调用）
 *   node scripts/douyin.js hot 50 --filter 明星 --json
 */

const https = require('https');

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/121.0'
];

function getRandomUserAgent() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

// ─── 请求抖音热榜接口 ───────────────────────────────────────────
function fetchDouyinHotBoard() {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'www.douyin.com',
      path: '/aweme/v1/hot/search/list/',
      method: 'GET',
      headers: {
        'User-Agent': getRandomUserAgent(),
        'Accept': 'application/json',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://www.douyin.com/'
      }
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (error) {
          reject(new Error(`JSON 解析失败: ${error.message}`));
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(10000, () => {
      req.destroy();
      reject(new Error('请求超时'));
    });
    req.end();
  });
}

// ─── 将单条热搜词标准化 ──────────────────────────────────────────
function normalizeItem(item, index) {
  const word = item.word || '无标题';
  const sentenceId = item.sentence_id || '';
  const link = sentenceId
    ? `https://www.douyin.com/hot/${sentenceId}/${encodeURIComponent(word)}`
    : `https://www.douyin.com/search/${encodeURIComponent(word)}`;
  const coverUrls = item.word_cover && item.word_cover.url_list || [];

  return {
    rank: index + 1,
    title: word,
    popularity: item.hot_value || 0,
    link,
    sentenceId,
    groupId: item.group_id || '',
    cover: coverUrls[0] || null,
    coverUrls,
    label: item.label || null,
    videoCount: item.video_count || 0,
    eventTime: item.event_time || null,
    sentenceTag: item.sentence_tag || null,
  };
}

// ─── 将原始接口数据格式化为统一结构 ────────────────────────────
function formatHotBoard(data, limit = 50) {
  if (!data || !data.data || !data.data.word_list) return { hotList: [], trendingList: [] };

  const hotList = data.data.word_list
    .map((item, i) => normalizeItem(item, i))
    .slice(0, limit);

  // trending_list：实时上升热点（热度值为 0，但正在快速攀升）
  const rawTrending = data.data.trending_list || [];
  const trendingList = rawTrending
    .map((item, i) => normalizeItem(item, i))
    .slice(0, limit);

  return { hotList, trendingList, activeTime: data.data.active_time || null };
}

// ─── 按关键词过滤（标题含任意一个关键词即保留）──────────────────
//   keywords: string[]  关键词数组，全部转小写比较（大小写不敏感）
//   返回过滤后的数组，并重新生成 rank（展示序号）
function filterByKeywords(hotList, keywords) {
  if (!keywords || keywords.length === 0) return hotList;

  const lowerKeywords = keywords.map(k => k.trim().toLowerCase()).filter(Boolean);
  if (lowerKeywords.length === 0) return hotList;

  const filtered = hotList.filter(item =>
    lowerKeywords.some(kw => item.title.toLowerCase().includes(kw))
  );

  // 过滤后重新编号，保留原始榜单排名字段供参考
  return filtered.map((item, index) => ({
    ...item,
    displayRank: index + 1,   // 过滤结果内的展示序号
    originalRank: item.rank   // 原始榜单排名
  }));
}

// ─── 控制台输出 ─────────────────────────────────────────────────
function printHotBoard(hotList, keywords) {
  const title = keywords && keywords.length > 0
    ? `🔍 抖音热榜过滤结果（关键词：${keywords.join('、')}）TOP ${hotList.length}`
    : `🔥 抖音热榜 TOP ${hotList.length}`;

  console.log(title);
  console.log('='.repeat(70));
  console.log();

  if (hotList.length === 0) {
    console.log('⚠️  没有匹配关键词的热榜条目');
    return;
  }

  hotList.forEach((item) => {
    const rankLabel = item.displayRank !== undefined
      ? `${item.displayRank.toString().padStart(2, ' ')}. [原榜 #${item.originalRank}] ${item.title}`
      : `${item.rank.toString().padStart(2, ' ')}. ${item.title}`;

    console.log(rankLabel);
    console.log(`    🔥 热度: ${item.popularity.toLocaleString()}`);
    if (item.sentenceId) console.log(`    🆔 话题ID: ${item.sentenceId}`);
    if (item.label) console.log(`    🏷️  标签: ${item.label}`);
    if (item.videoCount) console.log(`    🎬 视频数: ${item.videoCount}`);
    if (item.cover) console.log(`    🖼️  封面: ${item.cover}`);
    console.log(`    🔗 链接: ${item.link}`);
    console.log();
  });
}

// ─── 解析命令行参数 ─────────────────────────────────────────────
function parseArgs(args) {
  const result = {
    command: args[0] || 'hot',
    limit: 50,
    keywords: [],
    jsonMode: args.includes('--json'),
  };

  const filterIndex = args.findIndex(a => a === '--filter' || a.startsWith('--filter='));
  if (filterIndex !== -1) {
    let filterValue = '';
    if (args[filterIndex].startsWith('--filter=')) {
      filterValue = args[filterIndex].split('=')[1];
    } else if (args[filterIndex + 1]) {
      filterValue = args[filterIndex + 1];
    }
    result.keywords = filterValue.split(',').map(k => k.trim()).filter(Boolean);
  }

  const limitArg = args[1];
  if (limitArg && !limitArg.startsWith('--')) {
    const parsed = parseInt(limitArg);
    if (!isNaN(parsed)) result.limit = parsed;
  }

  return result;
}

// ─── 主函数 ─────────────────────────────────────────────────────
async function main() {
  const { command, limit, keywords, jsonMode } = parseArgs(process.argv.slice(2));

  if (command !== 'hot') {
    console.log('用法:');
    console.log('  node scripts/douyin.js hot [数量] [--filter 关键词1,关键词2] [--json]');
    console.log('');
    console.log('示例:');
    console.log('  node scripts/douyin.js hot              # 获取热榜（默认50条）');
    console.log('  node scripts/douyin.js hot 20           # 获取前20条');
    console.log('  node scripts/douyin.js hot --filter 科技,体育    # 过滤');
    console.log('  node scripts/douyin.js hot --json       # JSON 输出（供程序调用）');
    process.exit(1);
  }

  try {
    if (!jsonMode) {
      if (keywords.length > 0) {
        console.log(`正在获取抖音热榜（关键词过滤：${keywords.join('、')}）...\n`);
      } else {
        console.log('正在获取抖音热榜...\n');
      }
    }

    const data = await fetchDouyinHotBoard();
    const { hotList, trendingList, activeTime } = formatHotBoard(data, limit);

    if (hotList.length === 0) {
      if (jsonMode) {
        console.log(JSON.stringify({ error: '未获取到热榜数据', hotList: [], trendingList: [] }));
      } else {
        console.log('❌ 未获取到热榜数据');
      }
      process.exit(1);
    }

    const finalList = filterByKeywords(hotList, keywords);
    const finalTrending = filterByKeywords(trendingList, keywords);

    if (jsonMode) {
      // JSON 模式：输出结构化数据，供 Python / 其他程序直接解析
      console.log(JSON.stringify({
        activeTime,
        hotList: finalList,
        trendingList: finalTrending,
        meta: {
          fetchedAt: new Date().toISOString(),
          limit,
          keywords: keywords.length > 0 ? keywords : null,
        },
      }));
    } else {
      printHotBoard(finalList, keywords);

      // 额外显示上升热点
      if (finalTrending.length > 0) {
        console.log('📈 实时上升热点');
        console.log('='.repeat(70));
        console.log();
        finalTrending.slice(0, 10).forEach((item) => {
          const rk = item.displayRank !== undefined ? item.displayRank : item.rank;
          console.log(`${rk.toString().padStart(2, ' ')}. ${item.title}`);
          console.log(`    🔗 链接: ${item.link}`);
          console.log();
        });
      }
    }
  } catch (error) {
    if (jsonMode) {
      console.log(JSON.stringify({ error: error.message, hotList: [], trendingList: [] }));
    } else {
      console.error(`❌ 获取热榜失败: ${error.message}`);
    }
    process.exit(1);
  }
}

main();
