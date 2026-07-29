// 냉장고 요리사 - 프론트엔드 로직

var pantry = [];          // 사용자가 담은 재료 목록
var lastResults = [];     // 모달에서 다시 꺼내 쓰기 위한 마지막 검색 결과

var QUICK_PICKS = [
  "계란", "밥", "김치", "대파", "양파", "감자", "두부", "당근",
  "돼지고기", "소고기", "닭고기", "참치캔", "우유", "치즈", "애호박", "콩나물"
];

var STATUS_LABEL = {
  ready: "바로 가능",
  almost: "1개만 더",
  partial: "조금 부족"
};

var $ = function (id) { return document.getElementById(id); };

function esc(text) {
  var div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

// ---------- 재료 담기 ----------

function renderChips() {
  var box = $("chips");
  box.innerHTML = "";
  pantry.forEach(function (name, index) {
    var chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = esc(name) + ' <button type="button" title="삭제">✕</button>';
    chip.querySelector("button").addEventListener("click", function () {
      pantry.splice(index, 1);
      renderChips();
      syncQuickButtons();
    });
    box.appendChild(chip);
  });
}

// "1공기", "300g" 처럼 분량만 적힌 토큰
var AMOUNT_ONLY = /^\d+(\.\d+)?\s*(kg|g|ml|l|개|알|장|모|대|줌|컵|봉지|봉|캔|쪽|단|마리|인분|공기|큰술|작은술|스푼|톨|통)?$/i;
// "밥1공기" 처럼 이름 뒤에 붙은 분량
var TRAILING_AMOUNT = /\d+(\.\d+)?\s*(kg|g|ml|l|개|알|장|모|대|줌|컵|봉지|봉|캔|쪽|단|마리|인분|공기|큰술|작은술|스푼|톨|통)?$/i;

function cleanName(raw) {
  var name = raw.trim();
  if (!name || AMOUNT_ONLY.test(name)) return "";   // 분량만 있는 조각은 버린다
  name = name.replace(TRAILING_AMOUNT, "").trim();
  return name;
}

function addIngredients(text) {
  if (!text) return;
  text.split(/[,\s;/·|]+/).forEach(function (piece) {
    var name = cleanName(piece);
    if (name && pantry.indexOf(name) === -1) {
      pantry.push(name);
    }
  });
  renderChips();
  syncQuickButtons();
}

function syncQuickButtons() {
  var buttons = document.querySelectorAll("#quick-buttons .quick");
  Array.prototype.forEach.call(buttons, function (btn) {
    if (pantry.indexOf(btn.dataset.name) !== -1) {
      btn.classList.add("on");
    } else {
      btn.classList.remove("on");
    }
  });
}

function buildQuickButtons() {
  var box = $("quick-buttons");
  QUICK_PICKS.forEach(function (name) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick";
    btn.textContent = name;
    btn.dataset.name = name;
    btn.addEventListener("click", function () {
      var at = pantry.indexOf(name);
      if (at === -1) {
        pantry.push(name);
      } else {
        pantry.splice(at, 1);
      }
      renderChips();
      syncQuickButtons();
    });
    box.appendChild(btn);
  });
}

// ---------- 검색 ----------

function search() {
  if (pantry.length === 0) {
    $("summary").classList.remove("hidden");
    $("summary").innerHTML = "재료를 하나 이상 입력해 주세요.";
    $("shopping").classList.add("hidden");
    $("results").innerHTML = "";
    return;
  }

  var body = {
    ingredients: pantry,
    use_staples: $("use-staples").checked,
    max_missing: parseInt($("max-missing").value, 10),
    category: $("category").value,
    max_time: $("max-time").value
  };

  $("results").innerHTML = '<div class="empty">찾는 중…</div>';

  fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  })
    .then(function (res) { return res.json(); })
    .then(render)
    .catch(function () {
      $("results").innerHTML = '<div class="empty">검색 중 오류가 발생했습니다. 서버가 실행 중인지 확인해 주세요.</div>';
    });
}

function render(data) {
  lastResults = data.results || [];

  var readyCount = lastResults.filter(function (r) { return r.status === "ready"; }).length;
  var summary = $("summary");
  summary.classList.remove("hidden");
  summary.innerHTML =
    "<strong>" + data.total + "개</strong> 레시피를 찾았어요. " +
    "그중 <strong>" + readyCount + "개</strong>는 지금 바로 만들 수 있습니다.";

  renderShopping(data.shopping_list || []);

  if (lastResults.length === 0) {
    $("results").innerHTML =
      '<div class="empty">조건에 맞는 요리가 없습니다.<br>재료를 더 추가하거나 "부족해도 괜찮은 재료" 개수를 늘려보세요.</div>';
    return;
  }

  $("results").innerHTML = "";
  lastResults.forEach(function (item, index) {
    $("results").appendChild(buildCard(item, index));
  });
}

function renderShopping(list) {
  var box = $("shopping");
  var useful = list.filter(function (item) { return item.count >= 2; }).slice(0, 5);

  if (useful.length === 0) {
    box.classList.add("hidden");
    return;
  }

  var html = "<h3>🛒 이것만 사면 만들 수 있는 요리가 늘어나요</h3>";
  useful.forEach(function (item) {
    html +=
      '<div class="buy-item"><b>' + esc(item.name) + "</b> — " +
      item.count + "개 요리에 필요 (" +
      esc(item.recipes.slice(0, 3).join(", ")) +
      (item.recipes.length > 3 ? " 외" : "") + ")</div>";
  });

  box.innerHTML = html;
  box.classList.remove("hidden");
}

function buildCard(item, index) {
  var card = document.createElement("article");
  card.className = "card";

  var html = "";
  html += '<div class="card-head">';
  html += '<h3 class="card-title">' + esc(item.name) + "</h3>";
  html += '<span class="badge ' + item.status + '">' + STATUS_LABEL[item.status] + "</span>";
  html += "</div>";

  html += '<div class="card-meta">' +
    esc(item.category) + " · " + item.time_minutes + "분 · " +
    esc(item.difficulty) + " · " + esc(item.servings) + "</div>";

  html += '<div class="bar"><span class="' + item.status + '" style="width:' +
    item.match_percent + '%"></span></div>';

  if (item.have.length) {
    html += '<div class="ing-line"><span class="k">있는 재료</span>' +
      '<span class="tag-have">' + esc(item.have.join(", ")) + "</span></div>";
  }

  if (item.missing.length) {
    html += '<div class="ing-line"><span class="k">부족한 재료</span>' +
      '<span class="tag-miss">' + esc(item.missing.join(", ")) + "</span></div>";
  }

  var subKeys = Object.keys(item.substitutes || {});
  if (subKeys.length) {
    var parts = subKeys.map(function (key) {
      return esc(key) + " → " + esc(item.substitutes[key].join("/")) + "로 대체 가능";
    });
    html += '<div class="ing-line"><span class="k">대체</span>' +
      '<span class="tag-sub">' + parts.join(" · ") + "</span></div>";
  }

  card.innerHTML = html;
  card.addEventListener("click", function () { openModal(index); });
  return card;
}

// ---------- 상세 보기 ----------

function openModal(index) {
  var item = lastResults[index];
  if (!item) return;

  var html = "";
  html += "<h2>" + esc(item.name) + "</h2>";
  html += '<div class="modal-meta">' +
    esc(item.category) + " · " + item.time_minutes + "분 · " +
    esc(item.difficulty) + " · " + esc(item.servings) + " · " +
    esc((item.tags || []).join(", ")) + "</div>";

  html += "<h4>필요한 재료</h4><ul>";
  item.main.forEach(function (ing) {
    var owned = item.have.indexOf(ing.name) !== -1 ||
                item.have.some(function (h) { return ing.name.indexOf(h) !== -1; });
    html += "<li>" + (owned ? "✅ " : "🛒 ") +
      esc(ing.name) + " <span style='color:#7d766c'>" + esc(ing.amount) + "</span></li>";
  });
  html += "</ul>";

  if (item.optional && item.optional.length) {
    html += "<h4>있으면 더 좋아요</h4><ul>";
    item.optional.forEach(function (ing) {
      html += "<li>" + esc(ing.name) + " <span style='color:#7d766c'>" + esc(ing.amount) + "</span></li>";
    });
    html += "</ul>";
  }

  if (item.seasoning && item.seasoning.length) {
    html += "<h4>양념</h4><ul><li>" + esc(item.seasoning.join(", ")) + "</li></ul>";
  }

  html += "<h4>만드는 법</h4><ol>";
  item.steps.forEach(function (step) { html += "<li>" + esc(step) + "</li>"; });
  html += "</ol>";

  if (item.tip) {
    html += '<div class="tip-box">💡 ' + esc(item.tip) + "</div>";
  }

  $("modal-content").innerHTML = html;
  $("modal").classList.remove("hidden");
}

function closeModal() {
  $("modal").classList.add("hidden");
}

// ---------- 초기화 ----------

document.addEventListener("DOMContentLoaded", function () {
  buildQuickButtons();

  var input = $("ingredient-input");

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addIngredients(input.value);
      input.value = "";
    }
  });

  $("add-btn").addEventListener("click", function () {
    addIngredients(input.value);
    input.value = "";
    input.focus();
  });

  $("search-btn").addEventListener("click", search);

  $("clear-btn").addEventListener("click", function () {
    pantry = [];
    renderChips();
    syncQuickButtons();
    $("summary").classList.add("hidden");
    $("shopping").classList.add("hidden");
    $("results").innerHTML = "";
  });

  $("modal").addEventListener("click", function (event) {
    if (event.target.dataset.close) closeModal();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
  });

  // 자동완성 목록 채우기
  fetch("/api/ingredients")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      var list = $("ingredient-list");
      (data.ingredients || []).forEach(function (name) {
        var option = document.createElement("option");
        option.value = name;
        list.appendChild(option);
      });
    })
    .catch(function () { /* 자동완성은 없어도 검색에 지장 없음 */ });
});
