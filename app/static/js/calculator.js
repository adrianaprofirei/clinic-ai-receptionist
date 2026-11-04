/* Lost-revenue calculator: "Câți pacienți pierzi lunar?"
 *
 * unanswered = inquiries × missed% ; lost = unanswered × conversion% (0.1 precision)
 * monthly = lost × value ; yearly = monthly × 12
 *
 * Mirrors app/calculator.py (checked by tests/test_calculator.py).
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ZambetCalculator = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var LIMITS = { inquiries: 100000, missedPct: 100, conversionPct: 100, valueRon: 1000000 };

  function num(value, max) {
    var n = parseFloat(String(value == null ? "" : value).replace(",", "."));
    return Number.isFinite(n) ? Math.min(Math.max(n, 0), max) : 0;
  }

  function group(n) {
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function decimal(n) {
    var tenths = Math.round(n * 10);
    var rest = tenths % 10;
    return group(Math.floor(tenths / 10)) + (rest ? "," + rest : "");
  }

  function compute(input) {
    var inquiries = num(input.inquiries, LIMITS.inquiries);
    var missedPct = num(input.missedPct, LIMITS.missedPct);
    var conversionPct = num(input.conversionPct, LIMITS.conversionPct);
    var valueRon = num(input.valueRon, LIMITS.valueRon);

    var unanswered = inquiries * missedPct / 100;
    var lost = Math.round(unanswered * conversionPct / 100 * 10) / 10;
    var monthly = Math.round(lost * valueRon);
    return {
      inquiries: inquiries,
      missedPct: missedPct,
      conversionPct: conversionPct,
      valueRon: valueRon,
      unanswered: unanswered,
      lostPatients: lost,
      monthlyRevenue: monthly,
      yearlyRevenue: monthly * 12,
      formatted: {
        inquiries: group(inquiries),
        missedPct: decimal(missedPct),
        conversionPct: decimal(conversionPct),
        valueRon: group(valueRon),
        unanswered: decimal(unanswered),
        lostPatients: decimal(lost),
        monthlyRevenue: group(monthly),
        yearlyRevenue: group(monthly * 12)
      }
    };
  }

  return { LIMITS: LIMITS, compute: compute, group: group, decimal: decimal, num: num };
});
