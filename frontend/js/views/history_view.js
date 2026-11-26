import { api } from "../api.js";
const { ref, onMounted } = Vue;
export const HistoryView = {
  setup() {
    const items = ref([]); const err = ref(""); const msg = ref("");
    const ticket = ref(""); const scope = ref("all");
    async function reload() {
      err.value = "";
      try {
        const q = new URLSearchParams({ limit: "200", scope: scope.value });
        if (ticket.value.trim()) q.set("ticket", ticket.value.trim());
        const res = await api(`/api/history?${q.toString()}`);
        items.value = res.items || [];
        msg.value = ticket.value.trim()
          ? `按单号「${ticket.value.trim()}」${scope.value === "official" ? "正式镜头" : scope.value === "shadow" ? "影子镜头" : "全部镜头"}检索到 ${items.value.length} 条`
          : `共 ${items.value.length} 条`;
      }
      catch (e) { err.value = e.message || String(e); }
    }
    function search() { reload(); }
    function reset() { ticket.value = ""; scope.value = "all"; reload(); }
    onMounted(reload);
    return { items, err, msg, ticket, scope, reload, search, reset };
  },
  template: `
  <div class="panel">
    <h2>变更历史 <span class="pill">单号双写跨表</span></h2>
    <p class="hint">发布说明中的变更单号会双写 releases 与 change_history。按单号可检索；“正式”镜头只含已发布/升正记录，未升正影子不会冒充齐套。</p>
    <div class="field" style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center">
      <input style="flex:1;min-width:240px" v-model="ticket" placeholder="输入变更单号检索，如 CHG-20261126-05" @keyup.enter="search" />
      <select v-model="scope" @change="reload">
        <option value="all">全部镜头</option>
        <option value="official">仅正式（已发布齐套）</option>
        <option value="shadow">仅影子（未升正）</option>
      </select>
      <button class="btn" @click="search">按单号检索</button>
      <button class="btn secondary" @click="reset">重置</button>
    </div>
    <p class="muted" v-if="msg" style="margin:.4rem 0">{{ msg }}</p>
    <div class="err" v-if="err">{{ err }}</div>
    <table>
      <thead><tr><th>性质</th><th>时间动作</th><th>开关</th><th>环境</th><th>变更单号</th><th>详情</th><th>操作者</th></tr></thead>
      <tbody>
        <tr v-for="it in items" :key="it.id" :class="{'row-shadow': it.stage==='shadow'}">
          <td>
            <span class="pill" :class="it.official ? 'ok' : 'warn'">{{ it.official ? '正式' : '影子' }}</span>
          </td>
          <td>{{ it.action }}</td>
          <td>{{ it.flag_key || '-' }}</td>
          <td>{{ it.env_code || '-' }}</td>
          <td><code>{{ it.change_ticket || '-' }}</code></td>
          <td>{{ it.detail }}</td>
          <td>{{ it.actor }}</td>
        </tr>
      </tbody>
    </table>
    <p class="muted" v-if="!items.length" style="margin-top:.7rem">当前镜头无记录（未升正影子不会出现在“仅正式”镜头）。</p>
    <button class="btn secondary" style="margin-top:0.7rem" @click="reload">刷新</button>
  </div>`
};
