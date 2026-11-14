'use strict';
const $ = (id) => document.getElementById(id);
let stage = 'empty', busy = false, resendUntil = 0, count = 0;
const names = {empty:'尚未开始',code_sent:'待验证邮箱',verified:'邮箱已验证',registered:'待激活',active:'会话已激活',ready:'可尝试兑换',redeemed:'兑换成功',uncertain:'需要确认结果'};
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const today = new Date();
$('dob').max = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
function log(message, error = false) {
  if (!count) $('activity-list').replaceChildren();
  const li = document.createElement('li');
  li.textContent = message; if (error) li.className = 'error';
  const timestamp = document.createElement('small');
  timestamp.textContent = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
  li.append(timestamp); $('activity-list').prepend(li);
  while ($('activity-list').children.length > 7) $('activity-list').lastChild.remove();
  $('event-count').textContent = String(++count).padStart(2,'0');
}
function feedback(message, kind = '') {
  $('feedback').hidden = !message; $('feedback').textContent = message;
  $('feedback').className = `feedback ${kind}`;
}
function render() {
  const step = ['empty','code_sent'].includes(stage) ? 1 : ['verified','registered'].includes(stage) ? 2 : 3;
  ['verify','profile','invite','uncertain'].forEach(p => $(`panel-${p}`).hidden = true);
  $(`panel-${stage === 'uncertain' ? 'uncertain' : ['verify','profile','invite'][step-1]}`).hidden = false;
  document.querySelectorAll('.step').forEach(e => {
    const n = Number(e.dataset.step); e.classList.toggle('current', n === step); e.classList.toggle('done', n < step);
    if (n === step) e.setAttribute('aria-current','step'); else e.removeAttribute('aria-current');
  });
  $('section-kicker').textContent = stage === 'uncertain' ? 'PAUSED / CHECK RESULT' : [`STEP 01 / VERIFY`,`STEP 02 / PROFILE`,`STEP 03 / INVITE`][step-1];
  $('card-status').textContent = names[stage] || stage;
  $('otp-form').hidden = stage !== 'code_sent';
  $('profile-form').hidden = stage === 'registered';
  $('activation-area').hidden = stage !== 'registered';
  $('wake-area').hidden = stage !== 'active';
  $('invite-form').hidden = stage !== 'ready';
  $('success-area').hidden = stage !== 'redeemed';
  document.body.classList.toggle('busy', busy);
  document.querySelectorAll('button').forEach(e => e.disabled = busy);
  $('email').disabled = busy || stage === 'code_sent';
  $('proxy').disabled = busy || stage === 'code_sent';
  updateCooldown();
}
function updateCooldown() {
  const left = Math.max(0, Math.ceil((resendUntil - Date.now())/1000));
  $('send-button').disabled = busy || left > 0;
  $('send-button').textContent = left > 0 ? `${left} 秒后可重新发送` : stage === 'code_sent' ? '重新发送验证码 ↗' : '发送验证码 ↗';
}
setInterval(updateCooldown, 1000);
async function post(action, data, label) {
  if (busy) return;
  busy = true; render(); feedback('正在处理，请勿关闭页面或重复提交。', 'busy');
  try {
    const response = await fetch(`/api/${action}`, {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
    const result = await response.json();
    if (result.stage) stage = result.stage;
    if (!response.ok) throw new Error(result.error || '请求失败，请确认结果后再操作。');
    if (action === 'send-otp') resendUntil = Date.now() + 60000;
    if (action === 'confirm-otp') $('code').value = '';
    if (result.own_code) { $('own-code').textContent = result.own_code; $('own-code-box').hidden = false; }
    if (action === 'reset') {
      ['email-form','otp-form','profile-form','invite-form'].forEach(id => $(id).reset());
      $('own-code').textContent = ''; $('own-code-box').hidden = true; resendUntil = 0;
      count=0; $('event-count').textContent='00'; $('activity-list').replaceChildren();
    }
    feedback(result.message); log(label); return true;
  } catch(error) {
    // Status inspection only: never automatically replay a potentially accepted write.
    try { const r = await fetch('/api/status'); if (r.ok) { const s=await r.json(); stage=s.stage; resendUntil=Date.now()+s.resend_after*1000; } } catch (_) { /* offline */ }
    feedback(error.message === 'Failed to fetch' ? '连接中断，操作可能已生效。请先确认官网结果，不要重复提交。' : error.message, 'error');
    log(`${label}未完成，请查看提示`, true);
  } finally { busy=false; render(); }
}
function form(id, action, label) {
  $(id).addEventListener('submit', async e => {
    e.preventDefault();
    const data=Object.fromEntries(new FormData(e.target));
    // Disabled email/proxy are retained when explicitly resending to the same recipient.
    if(action==='send-otp') { data.email=$('email').value; data.proxy=$('proxy').value; }
    await post(action,data,label);
  });
}
form('email-form','send-otp','发送验证码');
form('otp-form','confirm-otp','验证邮箱');
form('profile-form','register','提交注册资料');
form('invite-form','redeem','兑换邀请码');
$('activate-button').onclick=()=>post('activate',{},'激活会话');
$('wake-button').onclick=()=>post('wake',{},'准备兑换会话');
$('own-button').onclick=()=>post('own-code',{},'获取自己的邀请码');
$('reset-button').onclick=()=>$('reset-dialog').showModal();
$('cancel-reset').onclick=()=>$('reset-dialog').close();
$('confirm-reset').onclick=async()=>{ $('reset-dialog').close(); await post('reset',{},'已清除本地会话'); };
$('copy-button').onclick=async()=>{
  try { await navigator.clipboard.writeText($('own-code').textContent); feedback('邀请码已复制。'); }
  catch(_) { feedback('浏览器未允许复制，请手动选中邀请码复制。','error'); }
};
async function init() {
  busy=true; render();
  try {
    const r=await fetch('/api/status'); const s=await r.json(); if(!r.ok) throw Error();
    stage=s.stage; resendUntil=Date.now()+s.resend_after*1000;
    if(s.email) $('email').value=s.email;
    if(s.own_code) { $('own-code').textContent=s.own_code; $('own-code-box').hidden=false; }
    if(stage!=='empty') feedback('已恢复本地会话步骤。需要重新填写的输入不会存储在浏览器中。');
  } catch(_) { feedback('无法连接本地服务，请重新启动服务并刷新页面。','error'); }
  finally { busy=false; render(); }
}
init();
