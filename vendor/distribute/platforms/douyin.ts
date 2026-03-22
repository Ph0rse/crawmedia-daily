/**
 * Douyin (抖音) publisher via Chrome CDP.
 * Opens creator.douyin.com, uploads video, fills description.
 * EXPERIMENTAL: Douyin has aggressive anti-automation.
 */

import fs from 'node:fs';
import {
  launchChrome, getPageSession, clickElement, typeText, evaluate,
  waitForSelector, uploadFile, sleep, randomDelay,
  type Manifest, type PublishResult, type ChromeSession,
} from '../cdp-utils.ts';

const DOUYIN_URL = 'https://creator.douyin.com/creator-micro/content/upload';

const SELECTORS = {
  videoUpload: 'input[type="file"][accept*="video"]',
  titleInput: 'input[placeholder*="标题"], input[class*="title"]',
  descriptionEditor: '[contenteditable="true"], textarea[placeholder*="描述"], div[class*="editor"]',
  tagInput: 'input[placeholder*="话题"], input[class*="topic"]',
  /** 注意：勿使用 Playwright 的 :has-text()，CDP 里用的是 document.querySelector，不支持该伪类 */
  loginIndicator: 'img[class*="avatar"]',
};

/**
 * 在页面中查找「发布」主按钮中心坐标（抖音为 React，无稳定 class；勿用 Playwright :has-text）
 * 返回 JSON 字符串 "{x,y}" 或 "null"
 */
async function findPublishButtonCenterJson(session: ChromeSession): Promise<string> {
  return evaluate<string>(
    session,
    `
    (function() {
      const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      function isVisible(el) {
        if (!(el instanceof HTMLElement)) return false;
        const st = getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.1) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0;
      }
      const nodes = Array.from(document.querySelectorAll('button, [role="button"], [class*="Button"], [class*="button"]'));
      const candidates = [];
      for (const el of nodes) {
        if (!isVisible(el)) continue;
        if (el instanceof HTMLButtonElement && el.disabled) continue;
        if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') continue;
        const t = norm(el.innerText || el.textContent || '');
        if (!t || t.length > 24) continue;
        if (/定时|草稿|取消|返回|上一步|预览|编辑封面|更换/.test(t)) continue;
        if (t === '发布' || /^发布\\s*$/.test(t)) {
          el.scrollIntoView({ block: 'center', inline: 'nearest' });
          const rect = el.getBoundingClientRect();
          return JSON.stringify({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
        }
        if (/^发布/.test(t) && t.length <= 12 && !/视频/.test(t)) {
          candidates.push(el);
        }
      }
      if (candidates.length) {
        const el = candidates[0];
        el.scrollIntoView({ block: 'center', inline: 'nearest' });
        const rect = el.getBoundingClientRect();
        return JSON.stringify({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
      }
      return 'null';
    })()
    `,
  );
}

/** 二次确认弹窗里的「确定 / 确认」 */
async function findConfirmButtonCenterJson(session: ChromeSession): Promise<string> {
  return evaluate<string>(
    session,
    `
    (function() {
      const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      function isVisible(el) {
        if (!(el instanceof HTMLElement)) return false;
        const st = getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.1) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0;
      }
      for (const el of document.querySelectorAll('button, [role="button"]')) {
        if (!isVisible(el)) continue;
        const t = norm(el.innerText || el.textContent || '');
        if (t === '确定' || t === '确认' || t === '知道了' || /^确认发布/.test(t) || /^立即发布/.test(t)) {
          el.scrollIntoView({ block: 'center' });
          const rect = el.getBoundingClientRect();
          return JSON.stringify({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
        }
      }
      return 'null';
    })()
    `,
  );
}

async function clickAtCenter(session: ChromeSession, x: number, y: number): Promise<void> {
  await session.cdp.send(
    'Input.dispatchMouseEvent',
    { type: 'mousePressed', x, y, button: 'left', clickCount: 1 },
    { sessionId: session.sessionId },
  );
  await randomDelay(40, 120);
  await session.cdp.send(
    'Input.dispatchMouseEvent',
    { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 },
    { sessionId: session.sessionId },
  );
}

/**
 * 轮询直到找到发布按钮并点击；视频转码未完成时按钮可能延迟出现
 */
async function waitAndClickPublish(session: ChromeSession, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const raw = await findPublishButtonCenterJson(session);
    if (raw && raw !== 'null') {
      try {
        const { x, y } = JSON.parse(raw) as { x: number; y: number };
        await clickAtCenter(session, x, y);
        return true;
      } catch {
        /* continue polling */
      }
    }
    await sleep(900);
  }
  return false;
}

async function tryClickConfirmIfPresent(session: ChromeSession, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const raw = await findConfirmButtonCenterJson(session);
    if (raw && raw !== 'null') {
      try {
        const { x, y } = JSON.parse(raw) as { x: number; y: number };
        await clickAtCenter(session, x, y);
        return true;
      } catch {
        /* retry */
      }
    }
    await sleep(500);
  }
  return false;
}

/**
 * 点击发布后轮询验证是否真正成功。
 * 成功信号（任一满足）：
 *   1. URL 离开了 /upload 页面（跳转到管理页或成功页）
 *   2. 页面出现「发布成功」/「审核中」/「上传成功」等字样
 *   3. 上传表单消失（input[type="file"] 不再存在）
 *
 * 返回 { success: boolean; reason: string; url: string }
 */
async function verifyPublishSuccess(
  session: ChromeSession,
  originalUrl: string,
  timeoutMs: number,
): Promise<{ success: boolean; reason: string; finalUrl: string }> {
  const start = Date.now();

  while (Date.now() - start < timeoutMs) {
    const snapshot = await evaluate<{ url: string; bodyText: string; hasUploadInput: boolean }>(
      session,
      `(function() {
        const url = window.location.href;
        const bodyText = (document.body?.innerText || '').slice(0, 3000);
        const hasUploadInput = !!document.querySelector('input[type="file"][accept*="video"]');
        return { url, bodyText, hasUploadInput };
      })()`,
    );

    const { url, bodyText, hasUploadInput } = snapshot;
    const norm = (s: string) => s.replace(/\s+/g, '');

    // 1. URL 已离开上传页
    if (!url.includes('/upload') && url !== originalUrl) {
      return { success: true, reason: `URL跳转至: ${url}`, finalUrl: url };
    }

    // 2. 页面出现成功/审核关键字
    const successKeywords = ['发布成功', '审核中', '上传成功', '发布完成', '视频已提交', '正在审核', '发布申请已提交'];
    const matched = successKeywords.find(kw => norm(bodyText).includes(kw));
    if (matched) {
      return { success: true, reason: `页面出现「${matched}」`, finalUrl: url };
    }

    // 3. 上传表单消失（说明已提交离开了填写状态）
    if (!hasUploadInput && url.includes('/upload')) {
      // 再等一秒确认不是临时消失
      await sleep(1500);
      const check = await evaluate<boolean>(session, `!document.querySelector('input[type="file"][accept*="video"]')`);
      if (check) {
        return { success: true, reason: '上传表单已消失（已提交）', finalUrl: url };
      }
    }

    // 4. 出现明确的失败提示
    const failKeywords = ['发布失败', '上传失败', '提交失败', '请重新发布'];
    const failMatched = failKeywords.find(kw => norm(bodyText).includes(kw));
    if (failMatched) {
      return { success: false, reason: `页面出现失败提示「${failMatched}」`, finalUrl: url };
    }

    await sleep(1500);
  }

  // 超时：抓一次当前 URL 作为参考
  const finalUrl = await evaluate<string>(session, 'window.location.href');
  return {
    success: false,
    reason: `验证超时（${Math.round(timeoutMs / 1000)}s），当前页面: ${finalUrl}`,
    finalUrl,
  };
}

export async function publishToDouyin(manifest: Manifest, preview: boolean): Promise<PublishResult> {
  const douyinData = manifest.outputs.douyin;
  if (!douyinData) {
    return { platform: 'douyin', status: 'skipped', message: 'No Douyin content in manifest' };
  }

  if (!fs.existsSync(douyinData.video)) {
    return {
      platform: 'douyin',
      status: 'manual',
      message: `Video file not found: ${douyinData.video}. Upload manually.`,
    };
  }

  let launchResult;
  try {
    launchResult = await launchChrome(DOUYIN_URL, 'douyin');
  } catch (err) {
    return {
      platform: 'douyin',
      status: 'manual',
      message: `Chrome launch failed. Upload ${douyinData.video} to Douyin manually.`,
    };
  }

  const { cdp, chrome } = launchResult;

  try {
    await sleep(5000); // Douyin loads slowly

    let session: ChromeSession;
    try {
      session = await getPageSession(cdp, 'douyin.com');
    } catch {
      return {
        platform: 'douyin',
        status: 'assisted',
        message: 'Page opened. Please log in to Douyin creator, then retry.',
      };
    }

    // Check login
    const currentUrl = await evaluate<string>(session, 'window.location.href');
    if (currentUrl.includes('login')) {
      return {
        platform: 'douyin',
        status: 'assisted',
        message: 'Login required. Please scan QR to log in to Douyin, then run /distribute again.',
      };
    }

    // Upload video
    const hasUpload = await waitForSelector(session, SELECTORS.videoUpload, 8_000);
    if (hasUpload) {
      await uploadFile(session, SELECTORS.videoUpload, douyinData.video);
      console.log(`  Video uploaded: ${douyinData.video}`);
      // 转码/解析完成前「发布」按钮可能不可用，多等一会再填表单项
      await sleep(15000);
    }

    // Fill title
    const hasTitle = await waitForSelector(session, SELECTORS.titleInput, 5_000);
    if (hasTitle) {
      await clickElement(session, SELECTORS.titleInput);
      await randomDelay(300, 600);
      await typeText(session, douyinData.copy.title);
    }

    await randomDelay(300, 600);

    // Fill description
    const hasDesc = await waitForSelector(session, SELECTORS.descriptionEditor, 5_000);
    if (hasDesc) {
      await clickElement(session, SELECTORS.descriptionEditor);
      await randomDelay();
      await typeText(session, douyinData.copy.description);
    }

    // Add tags
    for (const tag of douyinData.copy.tags) {
      const hasTag = await waitForSelector(session, SELECTORS.tagInput, 3_000);
      if (hasTag) {
        await clickElement(session, SELECTORS.tagInput);
        await randomDelay(100, 300);
        await typeText(session, tag.replace(/^#/, ''));
        await sleep(500);
        await session.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
        }, { sessionId: session.sessionId });
        await session.cdp.send('Input.dispatchKeyEvent', {
          type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
        }, { sessionId: session.sessionId });
        await randomDelay(300, 600);
      }
    }

    if (preview) {
      return { platform: 'douyin', status: 'assisted', message: 'Content pre-filled in Douyin editor.' };
    }

    // ── 正式发布流程 ──────────────────────────────────────────────
    console.log('  Waiting for publish button (video may still be processing)...');
    const uploadUrl = await evaluate<string>(session, 'window.location.href');

    // 1. 轮询等到发布按钮可点
    const clicked = await waitAndClickPublish(session, 120_000);
    if (!clicked) {
      return {
        platform: 'douyin',
        status: 'assisted',
        message: 'Content filled but publish button not found in time. Finish manually.',
      };
    }
    console.log('  Publish button clicked. Checking for confirm dialog...');
    await randomDelay(400, 900);

    // 2. 处理可能出现的二次确认弹窗（最多等 12s）
    await tryClickConfirmIfPresent(session, 12_000);

    // 3. 等待并验证发布结果（最多 60s）
    console.log('  Verifying publish result...');
    const verify = await verifyPublishSuccess(session, uploadUrl, 60_000);

    if (verify.success) {
      console.log(`  ✅ Publish verified: ${verify.reason}`);
      return {
        platform: 'douyin',
        status: 'success',
        message: `Published to Douyin | ${verify.reason}`,
      };
    }

    // 验证失败：页面截图辅助诊断
    const diagUrl = await evaluate<string>(session, 'window.location.href');
    const diagText = await evaluate<string>(session, `(document.body?.innerText || '').slice(0, 500)`);
    console.log(`  ⚠️  Verify failed: ${verify.reason}`);
    console.log(`  Current URL: ${diagUrl}`);
    console.log(`  Page snippet: ${diagText.slice(0, 200)}`);

    return {
      platform: 'douyin',
      status: 'assisted',
      message: `Button clicked but publish not confirmed: ${verify.reason}. Check Douyin manually.`,
    };
  } catch (err) {
    return {
      platform: 'douyin',
      status: 'manual',
      message: `CDP error (Douyin anti-automation likely): ${err instanceof Error ? err.message : String(err)}`,
    };
  } finally {
    cdp.close();
  }
}
