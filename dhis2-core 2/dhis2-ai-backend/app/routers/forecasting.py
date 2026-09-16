"""
Forecasting router — runs ARIMA forecasts for 10 immunisation coverage KPIs.

Flow:
  1. Read numerator + denominator values from planning_data_ingest grouped by period
  2. Compute coverage rate = SUM(numerator) / SUM(denominator) * 100 per period
  3. Fit ARIMA on 7 historical annual points
  4. Forecast 3 years ahead (EFY2018–2020) with 95% confidence intervals
  5. Persist both historical rates and forecasts into forecast_results
  6. Return full series JSON for the frontend to render
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.database import get_db

router = APIRouter(prefix="/forecasting", tags=["forecasting"])

# The 10 KPIs: (label, numerator_id, denominator_id)
KPIS = [
    ("Penta-1 Coverage",          "gUTDQGm7wQ4", "eBLFFQCHD6R"),
    ("Penta-3 Coverage",          "UbbrqQ7B697", "z2kPnR9zAwf"),
    ("MCV1 Coverage",             "xegDuD9La4v", "xbIBbuMFd9W"),
    ("MCV2 Coverage",             "PfO4WUDlWuX", "gfq4lUY0P6z"),
    ("OPV3 Coverage",             "WG81RqwkVHW", "q53mBzytPAn"),
    ("PCV-3 Coverage",            "Uyy6Skhj4Yj", "I4JRfdQAe6N"),
    ("IPV Coverage",              "vNMWVmoyXmE", "tW3jSOi27n7"),
    ("Full Immunization Coverage","M8OvIia8OG5", "ZqLnqha9z4M"),
    ("PAB Coverage",              "d4vCK4A4FGG", "GPqX9cbF8Fp"),
    ("Rotavirus-2 Coverage",      "B9m2bnFVuHZ", "cGogkdmtsVr"),
]

# Historical EFY periods in order
HIST_PERIODS = [
    "2011July", "2012July", "2013July", "2014July",
    "2015July", "2016July", "2017July",
]
FORECAST_PERIODS = ["2018July", "2019July", "2020July"]

PERIOD_LABEL = {
    "2011July": "EFY2011", "2012July": "EFY2012", "2013July": "EFY2013",
    "2014July": "EFY2014", "2015July": "EFY2015", "2016July": "EFY2016",
    "2017July": "EFY2017", "2018July": "EFY2018", "2019July": "EFY2019",
    "2020July": "EFY2020",
}


def _fetch_coverage(db: Session, num_id: str, den_id: str) -> dict[str, float]:
    """Return {period: coverage_rate} for all historical periods."""
    sql = text("""
        SELECT
            period,
            SUM(CASE WHEN data_element = :num THEN value::numeric ELSE 0 END) AS numerator,
            SUM(CASE WHEN data_element = :den THEN value::numeric ELSE 0 END) AS denominator
        FROM planning_data_ingest
        WHERE data_element IN (:num, :den)
          AND source = 'dhis2-nextgen-integration'
        GROUP BY period
        ORDER BY period
    """)
    rows = db.execute(sql, {"num": num_id, "den": den_id}).fetchall()
    result = {}
    for row in rows:
        if row.denominator and row.denominator > 0:
            result[row.period] = round(float(row.numerator) / float(row.denominator) * 100, 4)
    return result


def _run_arima(values: list[float], n_forecast: int = 3):
    """Fit ARIMA and return (forecast, lower_ci, upper_ci) arrays."""
    import warnings
    import numpy as np
    warnings.filterwarnings("ignore")

    try:
        from pmdarima import auto_arima
        model = auto_arima(
            values,
            start_p=0, max_p=2,
            start_q=0, max_q=1,
            d=None, max_d=1,
            seasonal=False,
            information_criterion="aic",
            suppress_warnings=True,
            error_action="ignore",
        )
        fc, conf = model.predict(n_periods=n_forecast, return_conf_int=True, alpha=0.05)
        return (
            [round(float(v), 4) for v in fc],
            [round(float(v), 4) for v in conf[:, 0]],
            [round(float(v), 4) for v in conf[:, 1]],
        )
    except Exception:
        # Fallback: linear extrapolation if ARIMA fails
        x = np.arange(len(values))
        coeffs = np.polyfit(x, values, 1)
        fc = [round(float(np.polyval(coeffs, len(values) + i)), 4) for i in range(n_forecast)]
        std = float(np.std(np.array(values) - np.polyval(coeffs, x)))
        lower = [round(v - 1.96 * std, 4) for v in fc]
        upper = [round(v + 1.96 * std, 4) for v in fc]
        return fc, lower, upper


@router.post("/run")
def run_forecasts(db: Session = Depends(get_db)):
    """Run ARIMA forecasts for all 10 KPIs and persist results."""
    results = []

    # Clear previous forecast run
    db.execute(text("DELETE FROM forecast_results"))

    for kpi_name, num_id, den_id in KPIS:
        coverage = _fetch_coverage(db, num_id, den_id)

        # Build ordered historical series (use 0 if a period is missing)
        hist_values = [coverage.get(p, 0.0) for p in HIST_PERIODS]

        # Run ARIMA
        fc_values, lower_ci, upper_ci = _run_arima(hist_values)

        # Persist historical points
        for period, val in zip(HIST_PERIODS, hist_values):
            db.execute(text("""
                INSERT INTO forecast_results (kpi, period, value, lower_ci, upper_ci, is_forecast)
                VALUES (:kpi, :period, :value, NULL, NULL, FALSE)
            """), {"kpi": kpi_name, "period": period, "value": val})

        # Persist forecast points
        for period, val, lo, hi in zip(FORECAST_PERIODS, fc_values, lower_ci, upper_ci):
            db.execute(text("""
                INSERT INTO forecast_results (kpi, period, value, lower_ci, upper_ci, is_forecast)
                VALUES (:kpi, :period, :value, :lo, :hi, TRUE)
            """), {"kpi": kpi_name, "period": period, "value": val, "lo": lo, "hi": hi})

        # Build response series
        series = []
        for period, val in zip(HIST_PERIODS, hist_values):
            series.append({
                "period": PERIOD_LABEL[period],
                "value": val,
                "lower_ci": None,
                "upper_ci": None,
                "is_forecast": False,
            })
        for period, val, lo, hi in zip(FORECAST_PERIODS, fc_values, lower_ci, upper_ci):
            series.append({
                "period": PERIOD_LABEL[period],
                "value": val,
                "lower_ci": lo,
                "upper_ci": hi,
                "is_forecast": True,
            })

        results.append({"kpi": kpi_name, "series": series})

    db.commit()
    return {"status": "ok", "kpis_forecast": len(results), "results": results}


@router.get("/results")
def get_forecast_results(db: Session = Depends(get_db)):
    """Return the last stored forecast run."""
    rows = db.execute(text("""
        SELECT kpi, period, value, lower_ci, upper_ci, is_forecast
        FROM forecast_results
        ORDER BY kpi, period
    """)).fetchall()

    kpi_map: dict[str, list] = {}
    for row in rows:
        label = PERIOD_LABEL.get(row.period, row.period)
        kpi_map.setdefault(row.kpi, []).append({
            "period": label,
            "value": float(row.value),
            "lower_ci": float(row.lower_ci) if row.lower_ci is not None else None,
            "upper_ci": float(row.upper_ci) if row.upper_ci is not None else None,
            "is_forecast": row.is_forecast,
        })

    return {"results": [{"kpi": k, "series": v} for k, v in kpi_map.items()]}
