/* MoveDesk owner dashboard. All dynamic text goes through textContent (customer text is untrusted). */
(function () {
  "use strict";
  var STATUS = {
    PENDING_APPROVAL: "Needs approval", DEPOSIT_SENT: "Deposit sent", BOOKED: "Booked",
    BALANCE_SENT: "Balance sent", PAID: "Paid", REJECTED: "Rejected"
  };
  var STEPS = ["Quote", "Deposit", "Booked", "Balance", "Paid"];
  var STEP_AT = { PENDING_APPROVAL: 0, DEPOSIT_SENT: 1, BOOKED: 2, BALANCE_SENT: 3, PAID: 4 };
  var EVENT_LABEL = {
    quote_created: "Quote created from chat", deposit_sent: "Deposit invoice sent", deposit_paid: "Deposit paid",
    balance_sent: "Balance invoice sent", balance_paid: "Balance paid", rejected: "Request rejected",
    reminder_sent: "Reminder sent", discount_expired: "Early-pay discount ended, full invoice issued",
    paypal_error: "PayPal error", payment_not_confirmed: "Payment not confirmed by PayPal",
    unknown_invoice_event: "Webhook for unknown invoice",
    superseded_invoice_paid: "A replaced invoice was paid: check PayPal and refund or credit"
  };

  var state = { jobs: [], selected: null, cfg: {}, key: "", drawerStamp: "", gridApi: null };
  try { state.key = sessionStorage.getItem("md_key") || ""; } catch (e) { /* storage unavailable */ }

  function $(id) { return document.getElementById(id); }
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") el.textContent = attrs[k];
      else if (k === "class") el.className = attrs[k];
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] != null) el.setAttribute(k, attrs[k]);
    });
    (function add(list) {
      [].concat(list == null ? [] : list).forEach(function (c) {
        if (c == null || c === false || c === "") return;
        if (Array.isArray(c)) return add(c);  // nested lists of children
        el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
      });
    })(kids);
    return el;
  }
  function money(n, cur) {
    try { return new Intl.NumberFormat("en-US", { style: "currency", currency: cur || state.cfg.currency || "USD" }).format(n || 0); }
    catch (e) { return String(n); }
  }
  function when(ts) {
    var d = new Date(ts);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " +
      d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  function toast(msg) {
    var t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3200);
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (state.key) headers["X-Admin-Key"] = state.key;
    return fetch(path, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (r.status === 401) {
            var k = window.prompt("Admin key");
            if (k) { state.key = k; try { sessionStorage.setItem("md_key", k); } catch (e) { /* ignore */ } return api(path, opts); }
          }
          if (!r.ok) { var err = new Error(data.error || data.detail || ("Request failed (" + r.status + ")")); err.status = r.status; throw err; }
          return data;
        });
      });
  }

  /* ---------- job helpers ---------- */
  function dueNow(j) {
    if (j.status === "DEPOSIT_SENT") return j.quote.deposit;
    if (j.status === "BALANCE_SENT") return Math.round(j.balance_amount * (1 - (j.balance_discount_pct || 0) / 100) * 100) / 100;
    return null;
  }
  function nextAction(j) {
    return {
      PENDING_APPROVAL: "Review and approve", DEPOSIT_SENT: "Waiting for deposit", BOOKED: "Complete after the move",
      BALANCE_SENT: "Waiting for payment", PAID: "Done", REJECTED: "Closed"
    }[j.status];
  }
  function chip(status) { return h("span", { class: "chip " + status, text: STATUS[status] || status }); }

  /* ---------- top strip ---------- */
  function renderMetrics(m) {
    $("m-pending").textContent = String((m.by_status || {}).PENDING_APPROVAL || 0);
    $("m-out").textContent = money(m.outstanding, m.currency);
    $("m-col").textContent = money(m.collected, m.currency);
    $("m-days").textContent = m.avg_days_to_pay == null ? "n/a" : m.avg_days_to_pay < 1 ? "same day" : m.avg_days_to_pay + " d";
    var r = $("m-risk"); r.textContent = String((m.at_risk || []).length);
    r.classList.toggle("hot", (m.at_risk || []).length > 0);
  }

  /* ---------- job list (AG Grid, with a plain table if the CDN is unreachable) ---------- */
  function rowData() {
    return state.jobs.map(function (j) {
      return {
        id: j.id, customer: j.customer_name, move: j.job.move_type + ", " + j.job.rooms + (j.job.rooms === 1 ? " room" : " rooms"),
        status: j.status, total: j.quote.total, due: dueNow(j), p: j.p_on_time, next: nextAction(j), created: j.created
      };
    });
  }
  function selectJob(id) { state.selected = id; state.drawerStamp = ""; renderDrawer(); markSelection(); }
  function markSelection() {
    var rows = document.querySelectorAll("tr.row");
    rows.forEach(function (r) { r.classList.toggle("sel", r.dataset.id === state.selected); });
  }
  function renderGrid() {
    $("empty").hidden = state.jobs.length > 0;
    var data = rowData();
    if (window.agGrid && !state.useTable) {
      $("fallback").hidden = true; $("grid").hidden = state.jobs.length === 0;
      if (!state.gridApi) {
        state.gridApi = agGrid.createGrid($("grid"), {
          columnDefs: [
            { field: "id", headerName: "Ref", width: 112 },
            { field: "customer", headerName: "Customer", flex: 1.2, minWidth: 140 },
            { field: "move", headerName: "Move", flex: 1.2, minWidth: 150 },
            { field: "status", headerName: "Status", width: 150,
              cellRenderer: function (p) { return chip(p.value); },
              valueFormatter: function (p) { return STATUS[p.value]; } },
            { field: "total", headerName: "Quote", width: 100, type: "rightAligned",
              valueFormatter: function (p) { return money(p.value); } },
            { field: "due", headerName: "Open invoice", width: 125, type: "rightAligned",
              valueFormatter: function (p) { return p.value == null ? "" : money(p.value); } },
            { field: "p", headerName: "On time", width: 100, type: "rightAligned",
              cellRenderer: function (p) {
                if (p.value == null) return "";
                return h("span", { class: p.value < 0.5 ? "late" : "", text: Math.round(p.value * 100) + "%" });
              } }
          ],
          rowData: data, getRowId: function (p) { return p.data.id; }, rowSelection: "single", domLayout: "autoHeight", suppressCellFocus: true,
          defaultColDef: { sortable: true, resizable: true },
          onRowClicked: function (e) { selectJob(e.data.id); }
        });
      } else {
        state.gridApi.setGridOption("rowData", data);
      }
      return;
    }
    // fallback table
    $("grid").hidden = true; var fb = $("fallback"); fb.hidden = state.jobs.length === 0; fb.textContent = "";
    var t = h("table", { class: "plain" }, [
      h("thead", {}, h("tr", {}, ["Ref", "Customer", "Move", "Status", "Quote", "Open invoice"].map(function (x) { return h("th", { text: x }); }))),
      h("tbody", {}, data.map(function (d) {
        return h("tr", { class: "row", "data-id": d.id, tabindex: "0", onclick: function () { selectJob(d.id); },
          onkeydown: function (e) { if (e.key === "Enter") selectJob(d.id); } }, [
          h("td", { text: d.id }), h("td", { text: d.customer }), h("td", { text: d.move }), h("td", {}, chip(d.status)),
          h("td", { class: "mono", text: money(d.total) }), h("td", { class: "mono", text: d.due == null ? "" : money(d.due) })]);
      }))]);
    fb.appendChild(t); markSelection();
  }

  /* ---------- detail drawer ---------- */
  function route(status) {
    var at = STEP_AT[status]; var kids = [];
    STEPS.forEach(function (s, i) {
      if (i) kids.push(h("span", { class: "leg" + (i <= at ? " done" : "") }));
      kids.push(h("span", { class: "stop" + (i < at ? " done" : i === at && status !== "REJECTED" ? " now" : "") }));
    });
    return [h("div", { class: "route", role: "img", "aria-label": "Progress: " + (STATUS[status] || status) }, kids),
      h("div", { class: "route-labels" }, STEPS.map(function (s) { return h("span", { text: s }); }))];
  }

  function invoiceBlock(title, url, qr, amount, extra) {
    return h("section", {}, [
      h("h3", { text: title }),
      h("div", { class: "qr" }, [
        qr ? h("img", { src: qr, alt: "QR code to pay the invoice" }) : null,
        h("div", {}, [
          h("div", { class: "mono", text: "Amount due " + money(amount) }),
          url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: "Open customer invoice" }) : null,
          extra
        ])
      ])
    ]);
  }

  function actions(j) {
    var box = h("div", { class: "actions" });
    var err = h("div", { class: "err", role: "alert" });
    function run(label, fn) {
      return function (ev) {
        ev.target.disabled = true; err.textContent = "";
        fn().then(function () { toast(label); return refresh(true); })
          .catch(function (e) { err.textContent = e.message; ev.target.disabled = false; });
      };
    }
    if (j.status === "PENDING_APPROVAL") {
      var reason = h("input", { type: "text", placeholder: "Reason (optional)", "aria-label": "Rejection reason" });
      box.appendChild(h("button", { class: "btn primary", text: "Approve and send deposit invoice",
        onclick: run("Deposit invoice sent", function () { return api("/api/jobs/" + j.id + "/approve", { method: "POST" }); }) }));
      box.appendChild(h("div", { class: "row" }, [reason, h("button", { class: "btn danger", text: "Reject",
        onclick: run("Request rejected", function () { return api("/api/jobs/" + j.id + "/reject", { method: "POST", body: { reason: reason.value } }); }) })]));
    }
    if (j.status === "BOOKED") {
      var hrs = h("input", { id: "hrs", type: "number", step: "0.25", min: "0.25", max: "72", value: String(j.quote.billable_hours), "aria-label": "Actual hours worked" });
      var waive = h("input", { type: "checkbox", id: "waive" });
      box.appendChild(h("div", { class: "row" }, [h("label", { for: "hrs", text: "Actual hours" }), hrs]));
      box.appendChild(h("label", { class: "note" }, [waive, " Waive extra time charges"]));
      box.appendChild(h("button", { class: "btn primary", text: "Job done: send balance invoice",
        onclick: run("Balance invoice sent", function () {
          return api("/api/jobs/" + j.id + "/complete", { method: "POST", body: { actual_hours: parseFloat(hrs.value), waive_overtime: waive.checked } });
        }) }));
    }
    if (j.status === "DEPOSIT_SENT" || j.status === "BALANCE_SENT") {
      box.appendChild(h("button", { class: "btn quiet", text: "Customer paid cash: record it",
        onclick: run("Recorded and confirmed with PayPal", function () { return api("/api/jobs/" + j.id + "/record-cash", { method: "POST" }); }) }));
      if (state.cfg.paypal_mode === "mock") {
        var inv = j.status === "DEPOSIT_SENT" ? j.deposit_invoice : j.balance_invoice;
        box.appendChild(h("a", { href: "/mock-pay/" + encodeURIComponent(inv), target: "_blank", rel: "noopener", text: "Open the simulated pay page" }));
      }
    }
    box.appendChild(err);
    return box;
  }

  function renderDrawer() {
    var d = $("drawer");
    var j = state.jobs.filter(function (x) { return x.id === state.selected; })[0];
    if (!j) { return; }
    if (state.drawerStamp === j.updated + j.status) return;
    state.drawerStamp = j.updated + j.status;
    d.textContent = "";
    var q = j.quote, e = j.estimate;
    d.appendChild(h("h2", { text: j.customer_name }));
    d.appendChild(h("div", { class: "sub", text: j.id + " · " + j.customer_email }));
    d.appendChild(chip(j.status));
    route(j.status).forEach(function (n) { d.appendChild(n); });

    var lines = q.lines.map(function (l) {
      return h("tr", {}, [h("td", { text: l.name }), h("td", { class: "mono", text: money(l.amount, q.currency) })]);
    });
    lines.push(h("tr", { class: "total" }, [h("td", { text: "Total" }), h("td", { class: "mono", text: money(q.total, q.currency) })]));
    lines.push(h("tr", {}, [h("td", { text: "Deposit (" + Math.round(q.deposit_pct * 100) + "%)" }), h("td", { class: "mono", text: money(q.deposit, q.currency) })]));
    lines.push(h("tr", {}, [h("td", { text: "Balance" }), h("td", { class: "mono", text: money(q.balance, q.currency) })]));
    if (j.overtime_amount) lines.push(h("tr", {}, [h("td", { text: "Extra time" }), h("td", { class: "mono", text: money(j.overtime_amount, q.currency) })]));
    d.appendChild(h("section", {}, [h("h3", { text: "Quote" }), h("table", { class: "lines" }, lines),
      h("div", { class: "note", text: "Estimated " + e.hours + " h (likely range " + e.p10 + " to " + e.p90 + " h), price range " +
        money(e.price_range.low, q.currency) + " to " + money(e.price_range.high, q.currency) + ". Prices come from your rate card, not from AI." })]));

    if ((e.drivers || []).length || (j.assumptions || []).length) {
      var s = h("section", {}, [h("h3", { text: "Why this price" })]);
      if (e.drivers.length) s.appendChild(h("ul", { class: "tight" }, e.drivers.map(function (x) { return h("li", { text: x.factor + ": " + (x.hours > 0 ? "+" : "") + x.hours + " h" }); })));
      if (j.assumptions.length) {
        s.appendChild(h("div", { class: "note", text: "Assumed because the customer did not say:" }));
        s.appendChild(h("ul", { class: "tight" }, j.assumptions.map(function (x) { return h("li", { text: x }); })));
      }
      d.appendChild(s);
    }

    if (j.deposit_invoice && j.status !== "PAID" && j.status !== "BALANCE_SENT" && j.status !== "BOOKED") {
      d.appendChild(invoiceBlock("Deposit invoice", j.deposit_url, j.deposit_qr, q.deposit));
    }
    if (j.balance_invoice) {
      var extra = [];
      if (j.balance_discount_pct) extra.push(h("div", { class: "note", text: j.balance_discount_pct + "% early-pay discount until " + when(j.discount_deadline) }));
      if (j.p_on_time != null) extra.push(h("div", { class: "note", text: "Predicted: " + Math.round(j.p_on_time * 100) + "% chance of paying on time, about " + j.exp_days + " days" }));
      d.appendChild(invoiceBlock("Balance invoice", j.balance_url, j.status === "PAID" ? "" : j.balance_qr, dueNow(j) == null ? j.balance_amount : dueNow(j), extra));
    }
    if ((j.reminders || []).length) {
      d.appendChild(h("section", {}, [h("h3", { text: "Reminders sent" }),
        h("ul", { class: "tight" }, j.reminders.map(function (r) { return h("li", { text: "Day " + r.day + ", " + r.tone + ": " + r.text }); }))]));
    }
    d.appendChild(h("section", {}, [h("h3", { text: "Actions" }), actions(j)]));
    var tl = h("div", { class: "timeline" });
    d.appendChild(h("section", {}, [h("h3", { text: "Timeline" }), tl]));
    api("/api/jobs/" + j.id).then(function (full) {
      (full.events || []).forEach(function (ev) {
        tl.appendChild(h("div", {}, [h("time", { text: when(ev.ts) }), h("span", { text: EVENT_LABEL[ev.kind] || ev.kind })]));
      });
    }).catch(function () { /* timeline is optional */ });
  }

  /* ---------- data loop ---------- */
  function refresh(force) {
    return Promise.all([api("/api/jobs"), api("/api/metrics")]).then(function (res) {
      state.jobs = res[0]; renderMetrics(res[1]); renderGrid();
      if (force) state.drawerStamp = "";
      renderDrawer();
    });
  }

  function demoButtons() {
    document.querySelectorAll("[data-adv]").forEach(function (b) {
      b.addEventListener("click", function () {
        api("/api/demo/advance", { method: "POST", body: { days: parseFloat(b.dataset.adv) } })
          .then(function (r) { var n = r.tick.reminders.length, c = r.tick.discounts_expired.length;
            toast("Moved forward " + b.dataset.adv + " day(s). " + n + " reminder(s) sent, " + c + " discount(s) ended."); return refresh(true); })
          .catch(function (e) { toast(e.message); });
      });
    });
    $("seed").addEventListener("click", function () {
      api("/api/demo/seed", { method: "POST" }).then(function () { toast("Sample requests added"); return refresh(true); }).catch(function (e) { toast(e.message); });
    });
    $("reset").addEventListener("click", function () {
      if (!window.confirm("Delete all jobs and start over?")) return;
      api("/api/demo/reset", { method: "POST" }).then(function () { state.selected = null; $("drawer").textContent = ""; return refresh(true); }).catch(function (e) { toast(e.message); });
    });
  }

  /* ---------- copilot: answers from live data, actions only on click ---------- */
  var history = [];
  function runProposal(p, btn) {
    var path = "/api/jobs/" + encodeURIComponent(p.job_id) + "/";
    var call = p.action === "approve" ? api(path + "approve", { method: "POST" })
      : p.action === "reject" ? api(path + "reject", { method: "POST", body: { reason: p.reason } })
      : p.action === "send_reminder" ? api(path + "remind", { method: "POST", body: { tone: p.tone } })
      : api(path + "record-cash", { method: "POST" });
    btn.disabled = true;
    return call.then(function () {
      btn.replaceWith(h("span", { class: "done", text: "Done" }));
      return refresh(true).then(function () { selectJob(p.job_id); });
    }).catch(function (e) { btn.disabled = false; toast(e.message); });
  }
  function showAnswer(r) {
    var box = $("answer"); box.textContent = ""; box.hidden = false;
    box.appendChild(h("div", { class: "text", text: r.answer }));
    (r.proposals || []).forEach(function (p) {
      var btn = h("button", { class: "btn primary", type: "button", text: "Confirm" });
      btn.addEventListener("click", function () { runProposal(p, btn); });
      box.appendChild(h("div", { class: "proposal" }, [
        h("div", {}, [h("b", { text: p.label }), p.reason ? h("div", { class: "why", text: p.reason }) : null]), btn]));
    });
    box.appendChild(h("div", { class: "src", text: (r.source === "ai"
      ? "Answered by " + (r.provider || "AI") + " from your live job data. "
      : "Offline answer from your live job data. ") + "Nothing changes until you confirm." + (r.note ? " " + r.note : "") }));
  }
  function ask(q) {
    q = (q || "").trim(); if (!q) return Promise.resolve();
    $("askText").value = q; $("askBtn").disabled = true;
    var box = $("answer"); box.hidden = false; box.textContent = "Looking at your jobs…";
    return api("/api/copilot", { method: "POST", body: { message: q, history: history.slice(-8) } })
      .then(function (r) {
        history.push({ role: "user", text: q }, { role: "assistant", text: r.answer });
        showAnswer(r);
      })
      .catch(function (e) { box.textContent = e.message; })
      .then(function () { $("askBtn").disabled = false; });
  }
  $("ask").addEventListener("submit", function (e) { e.preventDefault(); ask($("askText").value); });
  document.querySelectorAll("#suggest button").forEach(function (b) {
    b.addEventListener("click", function () { ask(b.textContent); });
  });

  /* ---------- guided story for judges and the demo video ---------- */
  var SAMPLE = "Hi, I'm Youssef Adel, youssef.adel@example.com. 2 bedroom apartment on the 5th floor with no elevator, " +
    "moving to a ground floor place 15 km away. We need packing.";
  var tour = { i: 0, job: null, reply: "" };
  function jobNow() { return api("/api/jobs/" + tour.job); }
  function focusJob() { return refresh(true).then(function () { selectJob(tour.job); }); }
  var STORY = [
    { title: "A customer asks for a quote", text: function () { return "Late at night, a customer types their move into the company's chat in plain words. No phone call, no form."; },
      button: "Send the customer's message",
      run: function () {
        return api("/api/chat", { method: "POST", body: { message: SAMPLE } }).then(function (r) {
          if (!r.job_id) throw new Error("The sample message did not create a quote.");
          tour.job = r.job_id; tour.reply = r.reply; return focusJob();
        });
      } },
    { title: "The agent replied with a price range", text: function () { return "“" + tour.reply + "”\n\nHours come from a trained model. The price comes from the company's own rate card, never from the AI."; },
      button: "Look at it as the owner", run: focusJob },
    { title: "The owner sees why it costs that", text: function () { return "On the right: the price lines, what adds hours (stairs, packing) and every guess the agent made. Nothing has gone to the customer yet."; },
      button: "Approve and send the deposit invoice",
      run: function () { return api("/api/jobs/" + tour.job + "/approve", { method: "POST" }).then(focusJob); } },
    { title: "PayPal sent the deposit invoice", text: function () { return "The customer gets a PayPal invoice with a QR code, and a Pay deposit button appears in their chat."; },
      button: "The customer pays the deposit",
      run: function () { return jobNow().then(function (j) { return api("/api/demo/pay/" + j.deposit_invoice, { method: "POST" }); }).then(focusJob); } },
    { title: "Booked", text: function () { return "PayPal reported the payment, and MoveDesk read the invoice back from PayPal to confirm it before booking the move."; },
      button: "Finish the move (it ran 1.5 hours long)",
      run: function () {
        return jobNow().then(function (j) {
          return api("/api/jobs/" + tour.job + "/complete", { method: "POST", body: { actual_hours: j.quote.billable_hours + 1.5 } });
        }).then(focusJob);
      } },
    { title: "Final invoice sent", text: function () { return "Overtime was added at the rate card price, and a 5% discount rewards paying within 3 days. A second model predicted how likely this customer is to pay on time."; },
      button: "Fast-forward 4 days",
      run: function () { return api("/api/demo/advance", { method: "POST", body: { days: 4 } }).then(focusJob); } },
    { title: "The discount ended", text: function () { return "The customer did not pay within 3 days, so MoveDesk cancelled that invoice in PayPal and issued a new one at the full price."; },
      button: "Fast-forward 3 more days",
      run: function () { return api("/api/demo/advance", { method: "POST", body: { days: 3 } }).then(focusJob); } },
    { title: "A reminder went out on its own", text: function () { return "Reminders are timed by predicted risk: sooner and firmer for customers likely to pay late. Now ask the copilot."; },
      button: "Ask: who owes me money?", run: function () { return ask("Who owes me money?"); } },
    { title: "The copilot answers from live data", text: function () { return "It can only propose actions. Each one is a button, and nothing happens until the owner clicks it."; },
      button: "The customer pays the final invoice",
      run: function () { return jobNow().then(function (j) { return api("/api/demo/pay/" + j.balance_invoice, { method: "POST" }); }).then(focusJob); } },
    { title: "Paid", text: function () { return "From the first message to cash in the bank, with a person approving every price and PayPal moving every dollar."; },
      button: "Finish", run: function () { $("tour").hidden = true; return Promise.resolve(); } }
  ];
  function showStep() {
    var s = STORY[tour.i];
    $("tourStep").textContent = "Step " + (tour.i + 1) + " of " + STORY.length;
    $("tourTitle").textContent = s.title; $("tourText").textContent = s.text(); $("tourNext").textContent = s.button;
  }
  $("tourNext").addEventListener("click", function () {
    var b = $("tourNext"); b.disabled = true;
    STORY[tour.i].run().then(function () {
      if (tour.i < STORY.length - 1) { tour.i += 1; showStep(); }
    }).catch(function (e) { toast(e.message); }).then(function () { b.disabled = false; });
  });
  $("tourClose").addEventListener("click", function () { $("tour").hidden = true; });
  $("play").addEventListener("click", function () { tour = { i: 0, job: null, reply: "" }; showStep(); $("tour").hidden = false; $("tourNext").focus(); });

  api("/api/config").then(function (c) {
    state.cfg = c;
    $("company").textContent = c.company + " dispatch";
    $("mode").textContent = c.paypal_mode === "mock" ? "PayPal simulator" : "PayPal sandbox";
    if (c.paypal_mode === "mock") $("demo").classList.add("on");
    demoButtons();
    return refresh(true);
  }).catch(function (e) { toast(e.message); });
  setInterval(function () { refresh(false).catch(function () { /* next poll retries */ }); }, 6000);
})();
