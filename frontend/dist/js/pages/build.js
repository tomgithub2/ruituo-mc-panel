/* 实例详情 · 「建筑」Tab：建筑导入（无需机器人进服）
   ----------------------------------------------------------------------------
   数据来源：**真实后端**（`window.Build`，见 js/build-api.js）。
   `palette_unmapped` / `warnings` / `chunks_touched` / `engine_recommended` 一律**只渲染后端返回值**，
   前端不算、不估、不兜底编造（契约 §1.15 前端约束）。
   后端就绪后把 mock 调用换成 API.post/get 即可，渲染逻辑不变。
   契约铁律已内建：
     · palette_unmapped / warnings / chunks_touched / engine_recommended 只渲染后端返回值
     · 未通过预检不给「导入」按钮
     · 回滚必须二次确认
     · 引擎选项不在 engine_available 内则禁用
   ========================================================================== */
window.PagesBuild = (function () {

  var FORMAT_BADGE = {
    schem: 'gold', schematic: 'gold', nbt: 'ok', litematic: 'info',
    mcstructure: 'err', zip: 'info'
  };

  function render(ctx) {
    var iid = ctx.iid;
    var el = document.getElementById('tab-body');
    var st = {
      up: null,        // 上传记录
      rep: null,       // 预检报告
      task: null,      // 当前任务
      x: 100, y: 64, z: -200,
      dimension: 'overworld',
      rotate: '0', mirror: 'none',
      place_mode: 'only_air',
      replace_list: '',
      include_entities: false,
      include_biome: false,
      engine: '',
      running: false,
      hasWorldEdit: false,
      formats: null,
      err: ''
    };
    var poll = null;

    /* 真实环境应来自 GET /api/instances/{iid}；mock 下用页面上的实例对象兜底 */
    var inst = (window.Store.state.instances || []).filter(function (i) { return String(i.id) === String(iid); })[0];
    if (inst) st.running = !!inst.running;
    else if (ctx.instance) st.running = !!ctx.instance.running;
    st.hasWorldEdit = !!(ctx.hasWorldEdit);

    function draw() {
      var canImport = !!(st.rep && st.rep.ok && st.engine && st.task === null);
      el.innerHTML = '' +
        banner() +
        '<div class="grid cols-2">' +
          '<div>' + uploadCard() + '</div>' +
          '<div>' + optionsCard(canImport) + '</div>' +
        '</div>' +
        (st.rep ? '<div class="mt">' + reportCard() + '</div>' : '') +
        (st.task ? '<div class="mt">' + taskCard() + '</div>' : '') +
        '<div class="mt">' + historyCard() + '</div>';
      bind();
    }

    function banner() {
      return '<div class="page-head" style="margin-bottom:var(--sp-4)">' +
        '<div class="titles"><div class="kicker">Build Import</div>' +
        '<h1>建筑导入</h1>' +
        '<div class="sub"><b>无需机器人进服</b> —— 全程由面板 + 服务端自身完成，不用开假人、不用 OP 在线。</div></div>' +
        '<div class="acts">' +
          (st.running
            ? C.statusBadge('running', '实例运行中')
            : C.statusBadge('stopped', '实例已停止')) +
        '</div></div>' +
        '<div class="tip mb">三套引擎：' +
        '<b>引擎 A（插件 + RCON）</b>实例运行中即可用，需要装 WorldEdit 并开启 RCON；' +
        '<b>引擎 B（离线写 Anvil 区块）</b>无插件依赖，但<b>必须先停止实例</b>；' +
        '<b>引擎 C（原版 /place template）</b><b>无需任何插件、也无需停服</b>——' +
        '面板把建筑写成原版结构文件，再用服务端自带的 <span class="mono">/place template</span> 放进去，' +
        '只要实例<b>开着 RCON</b>且<b>正在运行</b>就可用。' +
        '面板按后端给出的 <span class="mono">engine_recommended</span> 预选，你也可以手动改。</div>';
    }

    /* ---------------------------------------------------------- 上传区 */
    function uploadCard() {
      var f = st.formats || { formats: [] };
      return '<div class="card" style="height:100%">' +
        '<div class="card-head"><h3>1. 上传建筑文件</h3>' +
          '<div class="right"><span class="badge">上限 ' + U.fmtSize(f.max_size || 0) + '</span></div></div>' +
        '<div class="drop-zone" id="b-drop">' + Icon.svg('uploadCloud', 26) +
          '<div class="mt">把 <b>.schem / .schematic / .nbt / .litematic / .zip</b> 拖进来</div>' +
          '<div class="small mt">或者 <button class="btn sm" id="b-pick">选择文件</button></div></div>' +
        '<input type="file" id="b-file" style="display:none" ' +
          'accept=".schem,.schematic,.nbt,.litematic,.mcstructure,.zip" />' +
        (st.up
          ? '<div class="card mt" style="background:var(--bg-sunken)">' +
              '<div class="row"><span class="badge ' + (FORMAT_BADGE[st.up.format] || '') + '">' +
                U.esc(st.up.format) + '</span>' +
              '<b class="mono">' + U.esc(st.up._name || '') + '</b>' +
              '<div class="spacer"></div><span class="mono small">' + U.fmtSize(st.up.size) + '</span></div>' +
              '<div class="kv mt"><div class="k">upload_id</div><div class="v">' + U.esc(st.up.upload_id) + '</div>' +
              '<div class="k">sha256</div><div class="v">' + U.esc(st.up.sha256) + '</div></div>' +
              '<div class="row tight mt"><button class="btn sm primary" id="b-preview">' +
                Icon.svg('search', 14) + '解析并预检</button>' +
              '<button class="btn sm ghost" id="b-clear">移除</button></div>' +
            '</div>'
          : '') +
        '<div class="kicker mb mt">支持的格式</div>' +
        '<div class="table-wrap"><table><thead><tr><th>扩展名</th><th>说明</th><th>备注</th></tr></thead><tbody>' +
          (f.formats || []).map(function (x) {
            return '<tr><td><span class="badge ' + (FORMAT_BADGE[x.ext] || '') + '">.' + U.esc(x.ext) + '</span></td>' +
              '<td class="small">' + U.esc(x.name) + '</td>' +
              '<td class="small faint">' + U.esc(x.note || '') +
              (x.needs_convert ? ' <span class="badge err">需转换</span>' : '') + '</td></tr>';
          }).join('') + '</tbody></table></div>' +
        '</div>';
    }

    /* ---------------------------------------------------------- 坐标与选项 */
    function optionsCard(canImport) {
      var rep = st.rep || {};
      var avail = rep.engine_available || [];
      /* 引擎短名。⚠️ 必须与后端 plan.py 的 engine 标识一致：
       plugin / offline / vanilla_place —— 少一个就会出现"后端推荐了、
       UI 却连名字都显示不出来、按钮也点不了"的情况（vanilla_place 就漏过）。 */
    var ENGINE_NAME = {
      plugin: '引擎 A · 插件 + RCON',
      offline: '引擎 B · 离线写区块',
      vanilla_place: '引擎 C · 原版免插件'
    };
      return '<div class="card" style="height:100%">' +
        '<div class="card-head"><h3>2. 坐标与选项</h3>' +
          '<div class="right"><span class="mono small">' +
            (rep.bbox ? 'bbox ' + rep.bbox.min.join(',') + ' → ' + rep.bbox.max.join(',') : '未预检') +
          '</span></div></div>' +
        minimap() +
        '<div class="grid cols-3 tight mt">' +
          stepper('x', 'X', st.x) + stepper('y', 'Y', st.y) + stepper('z', 'Z', st.z) +
        '</div>' +
        '<div class="grid cols-2 tight">' +
          '<div class="field"><label>维度</label><select id="b-dim">' +
            [['overworld', '主世界'], ['nether', '下界'], ['end', '末地']].map(function (o) {
              return '<option value="' + o[0] + '"' + (st.dimension === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
            }).join('') + '</select></div>' +
          '<div class="field"><label>放置模式</label><select id="b-mode">' +
            [['only_air', '仅填充空气（最安全，默认）'], ['replace_all', '覆盖全部'], ['replace_list', '只替换清单内的方块']]
              .map(function (o) {
                return '<option value="' + o[0] + '"' + (st.place_mode === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
              }).join('') + '</select></div>' +
        '</div>' +
        (st.place_mode === 'replace_list'
          ? '<div class="field"><label>要替换的方块清单（逗号或换行分隔）</label>' +
            '<textarea class="mono" id="b-repl" placeholder="minecraft:stone, minecraft:dirt">' + U.esc(st.replace_list) + '</textarea></div>'
          : '') +
        '<div class="grid cols-2 tight">' +
          '<div class="field"><label>旋转</label><div class="btn-group" id="b-rot">' +
            ['0', '90', '180', '270'].map(function (r) {
              return '<button class="btn sm' + (st.rotate === r ? ' active' : '') + '" data-rot="' + r + '">' + r + '°</button>';
            }).join('') + '</div></div>' +
          '<div class="field"><label>镜像</label><div class="btn-group" id="b-mir">' +
            [['none', '无'], ['x', 'X'], ['z', 'Z'], ['xy', 'XY']].map(function (m) {
              return '<button class="btn sm' + (st.mirror === m[0] ? ' active' : '') + '" data-mir="' + m[0] + '">' + m[1] + '</button>';
            }).join('') + '</div></div>' +
        '</div>' +
        '<div class="row tight mb">' +
          '<label class="check"><input type="checkbox" id="b-ent"' + (st.include_entities ? ' checked' : '') + ' />包含实体</label>' +
          '<label class="check"><input type="checkbox" id="b-bio"' + (st.include_biome ? ' checked' : '') + ' />包含生物群系</label>' +
        '</div>' +
        '<div class="field"><label>执行引擎</label>' +
          '<div class="row tight">' + ['plugin', 'vanilla_place', 'offline'].map(function (e) {
            var ok = avail.indexOf(e) >= 0;
            var rec = rep.engine_recommended === e;
            return '<button class="btn sm' + (st.engine === e ? ' primary' : '') + '"' + (ok ? '' : ' disabled ') +
              'data-eng="' + e + '" title="' + (ok ? '' : '该引擎当前不可用') + '">' +
              U.esc(ENGINE_NAME[e]) + (rec ? ' ★推荐' : '') + '</button>';
          }).join('') + '</div>' +
          '<div class="desc">' +
            (avail.length
              ? '后端可用引擎：' + avail.map(function (e) { return U.esc(ENGINE_NAME[e] || e); }).join('、')
              : '后端未返回可用引擎（engine_available 为空）—— 请先满足前置条件。') +
          '</div></div>' +
        '<div class="row">' +
          '<button class="btn primary" id="b-import"' + (canImport ? '' : ' disabled') + '>' +
            Icon.svg('building', 15) + '开始导入</button>' +
          '<span class="small muted">' +
            (st.rep && st.rep.ok ? '' : '需先通过预检') + '</span>' +
        '</div>' +
        '</div>';
    }

    /* 俯视小地图式坐标选择器（本项目自有的交互） */
    function minimap() {
      var rep = st.rep || {};
      var size = rep.size || { x: 32, z: 32 };
      var G = 25;                     // 25×25 网格
      var span = 512;                 // 视窗覆盖 512 格
      var cell = span / G;
      // 建筑在网格中的投影范围
      var sx = Math.max(1, Math.round((size.x || 32) / cell));
      var sz = Math.max(1, Math.round((size.z || 32) / cell));
      var ox = Math.floor(G / 2) - Math.floor(sx / 2);
      var oz = Math.floor(G / 2) - Math.floor(sz / 2);
      var cells = '';
      for (var r = 0; r < G; r++) {
        for (var c = 0; c < G; c++) {
          var inB = (c >= ox && c < ox + sx && r >= oz && r < oz + sz);
          var isOrigin = (c === Math.floor(G / 2) && r === Math.floor(G / 2));
          cells += '<i class="mm-cell' + (inB ? ' in-bbox' : '') + (isOrigin ? ' origin' : '') +
            '" data-r="' + r + '" data-c="' + c + '"></i>';
        }
      }
      return '<div class="field"><label>' + Icon.svg('target', 13) +
          ' 俯视定位（点击或拖动红框移动建筑；网格 1 格 = ' + cell + ' 方块）</label>' +
        '<div class="minimap" id="b-mm" style="--mm-g:' + G + '">' +
          cells +
          '<div class="mm-cross"></div>' +
          '<div class="mm-bbox" style="left:calc(' + (ox / G * 100) + '% );top:calc(' + (oz / G * 100) +
            '%);width:calc(' + (sx / G * 100) + '%);height:calc(' + (sz / G * 100) + '%)"></div>' +
        '</div>' +
        '<div class="row between small mt">' +
          '<span class="mono">原点 X=' + st.x + ' Z=' + st.z + '</span>' +
          '<span class="mono">' +
            (rep.bbox ? '投影 ' + rep.size.x + '×' + rep.size.z + ' 格 · 跨 ' +
              Math.ceil(rep.size.x / 16) + '×' + Math.ceil(rep.size.z / 16) + ' 区块' : '未预检：尺寸未知') +
          '</span></div></div>';
    }

    function stepper(key, label, val) {
      return '<div class="field"><label>' + label + '</label>' +
        '<div class="stepper"><button class="btn xs" data-step="' + key + '|-1">−</button>' +
        '<input type="number" class="num" id="b-' + key + '" value="' + val + '" />' +
        '<button class="btn xs" data-step="' + key + '|1">＋</button></div></div>';
    }

    /* ---------------------------------------------------------- 预检报告 */
    function reportCard() {
      var r = st.rep || {};
      if (!r.ok) {
        return '<div class="card"><div class="card-head"><h3>3. 预检报告</h3>' +
          '<div class="right">' + C.statusBadge('crashed', '拒绝导入') + '</div></div>' +
          '<div class="tip err">' + U.esc(r.error || '预检失败') + '</div>' +
          '<div class="small muted mt">已填的坐标与选项会保留，改完可以重新预检。</div></div>';
      }
      var unmapped = r.palette_unmapped || [];
      return '<div class="card"><div class="card-head"><h3>3. 预检报告</h3>' +
        '<div class="right"><span class="badge ' + (FORMAT_BADGE[r.format] || '') + '">' +
          U.esc(r.format) + '</span>' +
          (r.requires_backup ? '<span class="badge gold">写入前强制备份</span>' : '') + '</div></div>' +
        '<div class="grid cols-4">' +
          C.metric('建筑尺寸', r.size.x + '×' + r.size.y + '×' + r.size.z) +
          C.metric('方块总数', String(r.blocks_total)) +
          C.metric('受影响方块', String(r.blocks_affected)) +
          C.metric('涉及区块', String(r.chunks_touched)) +
        '</div>' +
        '<div class="grid cols-2 mt">' +
          '<div><div class="kicker mb">目标范围 bbox</div>' +
            '<div class="crumbs-file">' + U.esc('min [' + r.bbox.min.join(', ') + ']  →  max [' + r.bbox.max.join(', ') + ']') + '</div>' +
            '<div class="kv mt">' +
              '<div class="k">维度</div><div class="v">' + U.esc(r.dimension) + '</div>' +
              '<div class="k">palette 总数</div><div class="v">' + r.palette_total + '</div>' +
              '<div class="k">推荐引擎</div><div class="v">' + U.esc(r.engine_recommended || '后端未提供') + '</div>' +
              '<div class="k">可用引擎</div><div class="v">' + U.esc((r.engine_available || []).join(', ') || '后端未提供') + '</div>' +
            '</div></div>' +
          '<div><div class="kicker mb">未映射方块（' + unmapped.length + '）</div>' +
            (unmapped.length
              ? '<div class="table-wrap"><table><thead><tr><th>方块</th><th class="mono">数量</th><th>标记</th></tr></thead><tbody>' +
                unmapped.map(function (u) {
                  var heavy = u.count > 10;
                  return '<tr><td class="mono">' + U.esc(u.name) + '</td>' +
                    '<td class="mono num">' + u.count + '</td>' +
                    '<td>' + (heavy ? '<span class="badge err">影响较大</span>'
                                    : '<span class="badge" style="color:var(--warning)">少量</span>') + '</td></tr>';
                }).join('') + '</tbody></table></div>'
              : '<div class="empty small">后端返回的 palette_unmapped 为空 —— 所有方块都能映射</div>') +
          '</div>' +
        '</div>' +
        '<div class="kicker mb mt">警告（' + (r.warnings || []).length + '）</div>' +
        ((r.warnings || []).length
          ? (r.warnings || []).map(function (w) { return '<div class="tip warn mt">' + U.esc(w) + '</div>'; }).join('')
          : '<div class="small muted">后端未返回警告</div>') +
        '<div class="desc mt">以上数值与清单全部来自后端预检结果，前端不做任何估算或补全。</div>' +
        '</div>';
    }

    /* ---------------------------------------------------------- 任务卡 */
    function taskCard() {
      var t = st.task || {};
      var STATE = { parsing: '解析文件', mapping: '映射方块', writing: '写入区块',
                    done: '完成', failed: '失败', cancelled: '已取消' };
      var stCls = t.state === 'done' ? 'running' : (t.state === 'failed' ? 'crashed' : 'starting');
      return '<div class="card"><div class="card-head"><h3>4. 导入任务</h3>' +
        '<div class="right">' + C.statusBadge(stCls, STATE[t.state] || t.state) +
          '<span class="mono small">' + (t.written_chunks || 0) + '/' + (t.total_chunks || 0) + ' 区块</span></div></div>' +
        '<div class="progress"><i style="width:' + (t.progress || 0) + '%"></i></div>' +
        '<div class="row between small mt"><span>' + U.esc(STATE[t.state] || '') + '</span>' +
          '<span class="mono num">' + (t.progress || 0) + '%</span></div>' +
        (t.error ? '<div class="tip err mt">' + U.esc(t.error) + '</div>' : '') +
        '<div class="row mt">' +
          (t.state === 'done' || t.state === 'failed' || t.state === 'cancelled' ? '' :
            '<button class="btn sm danger" id="b-cancel">取消任务</button>') +
          (t.state === 'done' && !t.rolled_back
            ? '<button class="btn sm" id="b-rollback">' + Icon.svg('rollback', 14) + '恢复导入前状态</button>'
            : '') +
          (t.rolled_back ? '<span class="badge gold">已回滚</span>' : '') +
          (t.state === 'done' && !t.rolled_back
            ? '<span class="small muted">写入 ' + (t.written_chunks || 0) + ' 个区块，可在任意时刻一键回滚</span>'
            : '') +
        '</div></div>';
    }

    function historyCard() {
      var h = st.history || [];                 // 由 loadHistory() 从真实接口拉取
      if (!h.length) {
        return '<div class="card">' + C.empty('building', '还没有导入过建筑',
          '把 schematic 拖进上面的上传区，填个坐标就能导入 —— 不需要机器人进服，也不需要 OP 账号在线。' +
          '支持 .schem / .schematic / .nbt / .litematic 与包含多个建筑的 .zip。', '') + '</div>';
      }
      return '<div class="card"><div class="card-head"><h3>导入记录</h3>' +
        '<div class="right"><span class="badge">' + h.length + '</span></div></div>' +
        '<div class="table-wrap"><table><thead><tr><th>文件</th><th>引擎</th><th>坐标</th>' +
        '<th class="mono">区块</th><th>状态</th><th style="text-align:right">操作</th></tr></thead><tbody>' +
        h.map(function (t) {
          var o = t.origin || [];
          var st8 = t.state || t.raw_status || '';
          return '<tr><td class="mono">' + U.esc(t.filename || t._name || '') + '</td>' +
            '<td><span class="badge">' + U.esc(t.engine_used || t.engine || '') + '</span></td>' +
            '<td class="mono num">' + [o[0], o[1], o[2]].join(', ') + '</td>' +
            '<td class="mono num">' + (t.total_chunks || 0) + '</td>' +
            '<td>' + (t.rolled_back ? '<span class="badge gold">已回滚</span>'
              : st8 === 'done' ? '<span class="badge ok">完成</span>'
              : st8 === 'failed' ? '<span class="badge err">失败</span>'
              : st8 === 'cancelled' ? '<span class="badge">已取消</span>'
              : '<span class="badge">' + U.esc(st8) + '</span>') + '</td>' +
            '<td style="text-align:right">' + (st8 === 'done' && t.has_backup && !t.rolled_back
              ? '<button class="btn xs" data-hist-roll="' + t.task_id + '">回滚</button>' : '') + '</td></tr>';
        }).join('') + '</tbody></table></div></div>';
    }

    /* 导入记录来自**真实任务列表**（GET …/build/tasks）；失败不阻塞页面，只是少一张卡片 */
    function loadHistory() {
      return Build.history(iid, 30).then(function (list) {
        st.history = list || [];
        st.historyLoaded = true;
      });
    }

    /* ---------------------------------------------------------- 交互 */
    function bind() {
      var drop = document.getElementById('b-drop');
      var fi = document.getElementById('b-file');
      var pick = document.getElementById('b-pick');
      if (pick) pick.addEventListener('click', function (e) { e.stopPropagation(); fi.click(); });
      if (drop) {
        drop.addEventListener('click', function () { fi.click(); });
        drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
        drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
        drop.addEventListener('drop', function (e) {
          e.preventDefault(); drop.classList.remove('over');
          if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
      }
      if (fi) fi.addEventListener('change', function (e) {
        if (e.target.files.length) handleFile(e.target.files[0]);
        e.target.value = '';
      });

      var pv = document.getElementById('b-preview');
      if (pv) pv.addEventListener('click', function () { doPreview(this); });
      var cl = document.getElementById('b-clear');
      if (cl) cl.addEventListener('click', function () { st.up = null; st.rep = null; draw(); });

      ['x', 'y', 'z'].forEach(function (k) {
        var i = document.getElementById('b-' + k);
        if (i) i.addEventListener('change', function () {
          st[k] = parseInt(i.value, 10) || 0;
          st.rep = null;            // 坐标改了必须重新预检（契约约束 2）
          draw();
        });
      });
      document.querySelectorAll('[data-step]').forEach(function (b) {
        b.addEventListener('click', function () {
          var p = b.getAttribute('data-step').split('|');
          st[p[0]] = (parseInt(st[p[0]], 10) || 0) + parseInt(p[1], 10);
          st.rep = null;
          draw();
        });
      });
      var dim = document.getElementById('b-dim');
      if (dim) dim.addEventListener('change', function () { st.dimension = dim.value; st.rep = null; draw(); });
      var mode = document.getElementById('b-mode');
      if (mode) mode.addEventListener('change', function () { st.place_mode = mode.value; draw(); });
      var repl = document.getElementById('b-repl');
      if (repl) repl.addEventListener('change', function () { st.replace_list = repl.value; });
      document.querySelectorAll('[data-rot]').forEach(function (b) {
        b.addEventListener('click', function () { st.rotate = b.getAttribute('data-rot'); st.rep = null; draw(); });
      });
      document.querySelectorAll('[data-mir]').forEach(function (b) {
        b.addEventListener('click', function () { st.mirror = b.getAttribute('data-mir'); st.rep = null; draw(); });
      });
      var ent = document.getElementById('b-ent');
      if (ent) ent.addEventListener('change', function () { st.include_entities = ent.checked; });
      var bio = document.getElementById('b-bio');
      if (bio) bio.addEventListener('change', function () { st.include_biome = bio.checked; });
      document.querySelectorAll('[data-eng]').forEach(function (b) {
        b.addEventListener('click', function () {
          if (b.disabled) return;
          st.engine = b.getAttribute('data-eng');
          draw();
        });
      });
      var im = document.getElementById('b-import');
      if (im) im.addEventListener('click', function () { doImport(this); });
      var cn = document.getElementById('b-cancel');
      if (cn) cn.addEventListener('click', function () {
        C.Modal.confirm('取消导入任务', '会在当前区块边界安全停止，已写入的部分需要回滚才能还原。确定取消？', function () {
          return C.withLoading(cn, Build.cancel(iid, st.task.task_id)).then(function (r) {
            if (!r || !r.ok) { Toast.err((r && r.error) || '取消失败'); return; }
            Toast.warn('已请求取消，等待当前区块写完');
          });
        }, true);
      });
      var rb = document.getElementById('b-rollback');
      if (rb) rb.addEventListener('click', function () { askRollback(st.task.task_id, this); });
      document.querySelectorAll('[data-hist-roll]').forEach(function (b) {
        b.addEventListener('click', function () { askRollback(b.getAttribute('data-hist-roll'), this); });
      });
      bindMinimap();
    }

    /* 小地图：点击/拖动移动建筑中心 */
    function bindMinimap() {
      var mm = document.getElementById('b-mm');
      if (!mm) return;
      var dragging = false;
      var cell = 512 / 25;
      function move(ev) {
        var r = mm.getBoundingClientRect();
        var cx = ev.clientX - r.left, cy = ev.clientY - r.top;
        if (cx < 0 || cy < 0 || cx > r.width || cy > r.height) return;
        // 把点击位置换算成"建筑中心"，再回推左上角坐标
        var size = (st.rep && st.rep.size) || { x: 32, z: 32 };
        var wx = Math.round((cx / r.width - 0.5) * 512) - Math.floor(size.x / 2);
        var wz = Math.round((cy / r.height - 0.5) * 512) - Math.floor(size.z / 2);
        // 吸附到 16 的整数倍，方便对齐区块
        st.x = Math.round(wx / 16) * 16;
        st.z = Math.round(wz / 16) * 16;
        st.rep = null;
        draw();
      }
      mm.addEventListener('mousedown', function (e) { dragging = true; move(e); });
      mm.addEventListener('mousemove', function (e) { if (dragging) move(e); });
      window.addEventListener('mouseup', function () { dragging = false; });
      mm.addEventListener('touchmove', function (e) {
        if (e.touches.length) { e.preventDefault(); move(e.touches[0]); }
      }, { passive: false });
    }

    function handleFile(file) {
      var max = (st.formats && st.formats.max_size) ? st.formats.max_size : 128 * 1024 * 1024;
      if (file.size > max) {
        Toast.err('文件超过上传上限（' + U.fmtSize(max) + '）');
        return;
      }
      st.upLoading = true; st.err = ''; draw();
      Build.upload(iid, file, function (pct) {
        /* 上传进度：直接反映到状态里，draw() 会画出来 */
        st.upPct = pct;
        var el = document.getElementById('b-upprog');
        if (el) el.textContent = pct + '%';
      }).then(function (r) {
        st.upLoading = false; st.upPct = 0;
        if (!r.ok) {
          st.err = r.error || '上传失败';
          Toast.err('上传失败：' + st.err);
          draw();
          return;
        }
        st.up = r;
        st.rep = null; st.task = null; st.err = '';
        Toast.ok('已上传 ' + (r.filename || file.name) + '（' + U.fmtSize(r.size || file.size) + '）');
        draw();
        doPreview();          // 上传后自动预检一次，少点一次
      });
    }

    function doPreview(btn) {
      if (!st.up) { Toast.warn('请先上传建筑文件'); return; }
      var run = function () {
        return Build.preview(iid, st.up.upload_id, {
          x: st.x, y: st.y, z: st.z, dimension: st.dimension,
          rotate: st.rotate, mirror: st.mirror, place_mode: st.place_mode,
          replace_list: st.replace_list, include_entities: st.include_entities,
          mc_version: st.mcVersion || '', engine: 'auto'
        }).then(function (r) {
          st.rep = r;
          st.engine = (r && r.ok && r.engine_recommended) ? r.engine_recommended : '';
          if (!r || !r.ok) Toast.err('预检未通过：' + ((r && r.error) || '未知原因'));
          draw();
        });
      };
      if (btn) C.withLoading(btn, run()); else run();
    }

    function doImport(btn) {
      if (!st.rep || !st.rep.ok) { Toast.warn('必须先通过预检'); return; }
      if (!st.engine) { Toast.warn('请选择执行引擎'); return; }
      C.withLoading(btn, Build.importBuild(iid, {
        upload_id: st.up.upload_id, x: st.x, y: st.y, z: st.z, dimension: st.dimension,
        rotate: st.rotate, mirror: st.mirror, place_mode: st.place_mode,
        replace_list: st.replace_list, include_entities: st.include_entities,
        mc_version: st.mcVersion || '', engine: st.engine, backup: true
      }).then(function (r) {
        if (!r.ok) { Toast.err(r.error); return; }
        st.task = Build.normalizeTask({ task_id: r.task_id, status: r.status || 'parsing',
                                        progress: 0, total: st.rep.chunks_touched });
        Toast.ok('导入任务已创建：' + r.task_id);
        draw();
        if (poll) clearInterval(poll);
        poll = setInterval(function () {
          if (!st.task) { clearInterval(poll); return; }
          Build.task(iid, st.task.task_id).then(function (t) {
            if (!t || t.ok === false) {         // 任务查不到（过期/被清理）：停止轮询并说明
              clearInterval(poll);
              st.err = (t && t.error) || '任务查询失败';
              draw();
              return;
            }
            var prev = st.task.state;
            st.task = t;
            draw();
            if (prev !== t.state && Build.isTerminal(t.state)) {
              clearInterval(poll);
              if (t.state === 'done') Toast.ok('导入完成：写入 ' + (t.written_chunks || 0) + ' 个区块');
              else if (t.state === 'failed') Toast.err('导入失败：' + (t.error || '未知原因'));
              else if (t.state === 'cancelled') Toast.warn('导入已取消');
            }
          });
        }, 700);
      }));
    }

    function askRollback(tid, btn) {
      C.Modal.confirm('恢复导入前状态',
        '会用导入前的 region 备份覆盖当前世界文件 —— 导入之后这段时间对同一区域的其它改动也会一起被覆盖。' +
        '还原前面板会再备份一次当前状态，可以反复横跳。确定继续？', function () {
          return C.withLoading(btn, Build.rollback(iid, tid)).then(function (r) {
            if (!r || !r.ok) { Toast.err((r && r.error) || '回滚失败'); return; }
            Toast.ok(r.note || '已回滚');
            draw();
          });
        }, true);
    }

    function destroy() { if (poll) clearInterval(poll); }

    /* 启动：先画骨架（上传上限等来自真实 /api/build/formats），再补齐格式与导入记录。
       两个请求失败都不阻塞页面 —— 只影响对应的卡片，并在卡片里说明原因。 */
    st.formatsLoading = true;
    draw();
    Build.formats().then(function (r) {
      st.formatsLoading = false;
      if (r && r.ok) {
        st.formats = r;
      } else {
        st.formats = { formats: [], max_size: 128 * 1024 * 1024 };
        st.formatsError = (r && r.error) || '读取支持格式失败';
      }
      draw();
    });
    loadHistory().then(draw);
    return { destroy: destroy };
  }

  return { render: render };
})();
