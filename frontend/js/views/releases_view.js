import { api } from "../api.js";
import { extractChangeTicket } from "../ticket.js";
const { ref, computed, onMounted } = Vue;
export const ReleasesView = {
  setup() {
    const official = ref([]); const shadows = ref([]);
    const form = ref({
      flag_id: 5, env_id: 1, kind: "percent", percent: 15, cohort_id: null,
      summary: "CHG-20261126-05 notify.batch_push 批量推送通道十五灰度", use_shadow: false,
    });
    const err = ref(""); const msg = ref("");
    const ticket = computed(() => extractChangeTicket(form.value.summary));
    async function reload() {
      const res = await api("/api/releases");
      official.value = res.official || []; shadows.value = res.shadows || [];
    }
    async function publish() {
      err.value = ""; msg.value = "";
      // 甲路径前端同源闸：说明必须含变更单号；后端写口仍会权威拒写。
      if (!ticket.value) {
        err.value = "发布说明须含变更单号（如 CHG-20261126-05）。grayroll-05 甲路径：缺单号整笔拒写，正式发布与规则零增，影子也不收。";
        return;
      }
      try {
        const body = { ...form.value, cohort_id: form.value.cohort_id || null };
        const res = await api("/api/releases/publish", { method: "POST", body: JSON.stringify(body) });
        msg.value = `已提交：${res.mode}｜单号 ${res.change_ticket || ticket.value}` +
          (res.version ? `｜v${res.version}` : "（草案，未升正前正式零改）");
        await reload();
      } catch (e) { err.value = e.message || String(e); }
    }
    async function promote(id) {
      err.value = "";
      try {
        const res = await api(`/api/releases/shadow/${id}/promote`, { method: "POST", body: "{}" });
        msg.value = `影子 ${id} 已升正｜单号 ${res.change_ticket || "-"}｜v${res.version}`;
        await reload();
      }
      catch (e) { err.value = e.message || String(e); }
    }
    onMounted(reload);
    return { official, shadows, form, err, msg, ticket, publish, promote, reload };
  },
  template: `
  <div class="grid">
    <div class="panel span-5">
      <h2>发布生效 <span class="pill">grayroll-05 甲路径</span></h2>
      <p class="hint">发布说明必须含变更单号；缺单号整笔拒写，releases 与规则正式行零增（也不开放影子侧门）。勾选影子则写带单号的影子草案，升正后才落正式区。</p>
      <div class="field"><label>开关ID（notify.batch_push=5）</label><input type="number" v-model.number="form.flag_id" /></div>
      <div class="field"><label>环境ID（dev=1 staging=2 prod=3）</label><input type="number" v-model.number="form.env_id" /></div>
      <div class="field"><label>类型</label>
        <select v-model="form.kind"><option>percent</option><option>cohort</option><option>percent_and_cohort</option></select>
      </div>
      <div class="field"><label>比例</label><input type="number" v-model.number="form.percent" /></div>
      <div class="field">
        <label>发布说明（须含变更单号，如 CHG-20261126-05 / 变更单号：2026112605）</label>
        <textarea v-model="form.summary" rows="3"></textarea>
        <p v-if="ticket" class="hint" style="color:#1a7f37;margin:4px 0 0">已识别变更单号：<b>{{ ticket }}</b></p>
        <p v-else class="err" style="margin:4px 0 0">未识别变更单号：提交将被整笔拒写，正式零增。</p>
      </div>
      <div class="field"><label><input type="checkbox" v-model="form.use_shadow" /> 使用影子草案（说明仍须含同一变更单号；未升正前正式区零改）</label></div>
      <button class="btn" @click="publish">提交发布</button>
      <p class="muted" v-if="msg" style="color:#1a7f37">{{ msg }}</p>
      <div class="err" v-if="err">{{ err }}</div>
    </div>
    <div class="panel span-7">
      <h2>正式发布</h2>
      <table><thead><tr><th>开关</th><th>环境</th><th>版本</th><th>变更单号</th><th>说明</th></tr></thead>
      <tbody>
        <tr v-for="r in official" :key="r.id">
          <td>{{ r.flag_key }}</td><td>{{ r.env_code }}</td><td>v{{ r.version }}</td>
          <td><code>{{ r.change_ticket || '-' }}</code></td><td>{{ r.summary }}</td>
        </tr>
      </tbody></table>
      <h2 style="margin-top:1rem">影子发布（未升正不计正式齐套）</h2>
      <table><thead><tr><th>ID</th><th>开关</th><th>单号</th><th>状态</th><th></th></tr></thead>
      <tbody>
        <tr v-for="s in shadows" :key="'s'+s.id">
          <td>{{ s.id }}</td><td>{{ s.flag_id }}</td>
          <td><code>{{ s.change_ticket || '-' }}</code></td>
          <td><span class="pill">{{ s.status }}</span></td>
          <td><button class="btn secondary" v-if="s.status==='open'" @click="promote(s.id)">升正</button></td>
        </tr>
      </tbody></table>
    </div>
  </div>`
};
