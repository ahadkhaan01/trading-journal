from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, date
from collections import defaultdict
import os
import csv
import io


app = Flask(__name__)

app.config["SECRET_KEY"] = "trading-journal-secret-key"

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///trading_journal.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["UPLOAD_FOLDER"] = os.path.join(
    app.root_path,
    "static",
    "uploads"
)

db = SQLAlchemy(app)


# ============================================================
# MODELS
# ============================================================

class User(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email = db.Column(
        db.String(150),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(255),
        nullable=False
    )

    capital_usdt = db.Column(
        db.Float,
        default=0
    )

    trades = db.relationship(
        "Trade",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan"
    )


class Trade(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    date = db.Column(
        db.String(20),
        nullable=False
    )

    day = db.Column(
        db.String(20)
    )

    ticker = db.Column(
        db.String(50),
        nullable=False
    )

    direction = db.Column(
        db.String(20),
        default="LONG"
    )

    result = db.Column(
        db.String(20),
        default="LOSS"
    )

    buying_average = db.Column(
        db.Float,
        default=0
    )

    selling_average = db.Column(
        db.Float,
        default=0
    )

    tp = db.Column(
        db.Float,
        default=0
    )

    sl = db.Column(
        db.Float,
        default=0
    )

    risk = db.Column(
        db.Float,
        default=0
    )

    reason = db.Column(
        db.Text
    )

    strategy = db.Column(
        db.String(100)
    )

    notes = db.Column(
        db.Text
    )

    screenshot = db.Column(
        db.String(255)
    )

    risk_amount = db.Column(
        db.Float,
        default=0
    )

    position_size = db.Column(
        db.Float,
        default=0
    )

    pnl_percent = db.Column(
        db.Float,
        default=0
    )

    pnl_money = db.Column(
        db.Float,
        default=0
    )


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_database():

    inspector = db.inspect(db.engine)

    tables = inspector.get_table_names()

    if "trade" not in tables:
        return

    existing_columns = [
        column["name"]
        for column in inspector.get_columns("trade")
    ]

    migrations = {
        "direction": "VARCHAR(20) DEFAULT 'LONG'",
        "result": "VARCHAR(20) DEFAULT 'LOSS'",
        "strategy": "VARCHAR(100)",
        "notes": "TEXT",
        "screenshot": "VARCHAR(255)",
        "risk_amount": "FLOAT DEFAULT 0",
        "position_size": "FLOAT DEFAULT 0",
        "pnl_percent": "FLOAT DEFAULT 0",
        "pnl_money": "FLOAT DEFAULT 0"
    }

    for column, definition in migrations.items():

        if column not in existing_columns:

            db.session.execute(
                db.text(
                    f"ALTER TABLE trade ADD COLUMN {column} {definition}"
                )
            )

    db.session.commit()


# ============================================================
# HELPERS
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    return db.session.get(
        User,
        user_id
    )


def safe_float(value, default=0):

    try:
        if value is None or value == "":
            return default

        return float(value)

    except (ValueError, TypeError):
        return default


# ============================================================
# TRADE CALCULATOR
# ============================================================

def calculate_trade(
    capital,
    risk_percent,
    sl_percent,
    tp_percent,
    result,
    entry,
    exit_price,
    direction
):

    capital = safe_float(capital)

    risk_percent = safe_float(risk_percent)

    sl_percent = safe_float(sl_percent)

    tp_percent = safe_float(tp_percent)

    entry = safe_float(entry)

    exit_price = safe_float(exit_price)

    risk_amount = (
        capital * risk_percent / 100
    )

    if sl_percent > 0:

        position_size = (
            risk_amount /
            (sl_percent / 100)
        )

    else:

        position_size = 0


    # LOSS

    if result == "LOSS":

        pnl_money = -risk_amount

        pnl_percent = -risk_percent


    # WIN

    elif result == "WIN":

        if entry > 0 and exit_price > 0:

            if direction == "LONG":

                movement = (
                    exit_price - entry
                ) / entry * 100

            else:

                movement = (
                    entry - exit_price
                ) / entry * 100

            pnl_percent = movement

            pnl_money = (
                position_size *
                movement / 100
            )

        else:

            pnl_percent = tp_percent

            pnl_money = (
                position_size *
                tp_percent / 100
            )


    # BREAKEVEN

    else:

        pnl_percent = 0

        pnl_money = 0


    return {
        "risk_amount": risk_amount,
        "position_size": position_size,
        "pnl_percent": pnl_percent,
        "pnl_money": pnl_money
    }


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    if "user_id" in session:

        return redirect(
            url_for("dashboard")
        )

    return redirect(
        url_for("login")
    )


# ============================================================
# SIGNUP
# ============================================================

@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        capital = safe_float(
            request.form.get(
                "capital_usdt"
            )
        )

        if not email or not password:

            return render_template(
                "signup.html",
                error="Email and password are required."
            )

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:

            return render_template(
                "signup.html",
                error="Email already registered."
            )

        user = User(

            email=email,

            password=generate_password_hash(
                password
            ),

            capital_usdt=capital

        )

        db.session.add(user)

        db.session.commit()

        session["user_id"] = user.id

        return redirect(
            url_for("dashboard")
        )

    return render_template(
        "signup.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        user = User.query.filter_by(
            email=email
        ).first()

        if user:

            valid_password = False

            try:

                valid_password = check_password_hash(
                    user.password,
                    password
                )

            except Exception:

                valid_password = (
                    user.password == password
                )

            if valid_password:

                session["user_id"] = user.id

                return redirect(
                    url_for("dashboard")
                )

        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD + ADVANCED ANALYTICS
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    user = get_current_user()

    if not user:
        session.clear()

        return redirect(
            url_for("login")
        )


    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------

    search = request.args.get(
        "search",
        ""
    ).strip()

    result_filter = request.args.get(
        "result",
        ""
    )

    direction_filter = request.args.get(
        "direction",
        ""
    )


    query = Trade.query.filter_by(
        user_id=user.id
    )


    if search:

        query = query.filter(
            db.or_(
                Trade.ticker.ilike(
                    f"%{search}%"
                ),

                Trade.strategy.ilike(
                    f"%{search}%"
                )
            )
        )


    if result_filter:

        query = query.filter_by(
            result=result_filter
        )


    if direction_filter:

        query = query.filter_by(
            direction=direction_filter
        )


    trades = query.order_by(
        Trade.date.asc(),
        Trade.id.asc()
    ).all()


    # --------------------------------------------------------
    # BASIC STATISTICS
    # --------------------------------------------------------

    total_trades = len(trades)

    winning_trades = sum(
        1
        for trade in trades
        if trade.result == "WIN"
    )

    losing_trades = sum(
        1
        for trade in trades
        if trade.result == "LOSS"
    )

    breakeven_trades = sum(
        1
        for trade in trades
        if trade.result == "BREAKEVEN"
    )


    total_pnl = sum(
        safe_float(trade.pnl_money)
        for trade in trades
    )


    win_rate = (
        winning_trades /
        total_trades *
        100
        if total_trades
        else 0
    )


    # --------------------------------------------------------
    # WIN / LOSS ANALYTICS
    # --------------------------------------------------------

    winning_pnls = [
        safe_float(trade.pnl_money)
        for trade in trades
        if trade.result == "WIN"
    ]


    losing_pnls = [
        abs(
            safe_float(trade.pnl_money)
        )
        for trade in trades
        if trade.result == "LOSS"
    ]


    avg_win = (
        sum(winning_pnls) /
        len(winning_pnls)
        if winning_pnls
        else 0
    )


    avg_loss = (
        sum(losing_pnls) /
        len(losing_pnls)
        if losing_pnls
        else 0
    )


    gross_profit = sum(
        value
        for value in winning_pnls
        if value > 0
    )


    gross_loss = sum(
        losing_pnls
    )


    if gross_loss > 0:

        profit_factor = (
            gross_profit /
            gross_loss
        )

    elif gross_profit > 0:

        profit_factor = "∞"

    else:

        profit_factor = 0


    # --------------------------------------------------------
    # BEST / WORST TRADE
    # --------------------------------------------------------

    pnl_values = [
        safe_float(trade.pnl_money)
        for trade in trades
    ]


    best_trade = (
        max(pnl_values)
        if pnl_values
        else 0
    )


    worst_trade = (
        min(pnl_values)
        if pnl_values
        else 0
    )


    # --------------------------------------------------------
    # AVERAGE R:R
    # --------------------------------------------------------

    rr_values = []

    for trade in trades:

        sl = safe_float(
            trade.sl
        )

        tp = safe_float(
            trade.tp
        )

        if sl > 0:

            rr_values.append(
                tp / sl
            )


    avg_rr = (
        sum(rr_values) /
        len(rr_values)
        if rr_values
        else 0
    )


    # --------------------------------------------------------
    # TOTAL RISK
    # --------------------------------------------------------

    total_risk = sum(
        safe_float(
            trade.risk_amount
        )
        for trade in trades
    )


    # --------------------------------------------------------
    # MAX DRAWDOWN
    # --------------------------------------------------------

    equity = safe_float(
        user.capital_usdt
    )

    peak_equity = equity

    max_drawdown = 0

    equity_values = []

    equity_labels = []


    for trade in trades:

        equity += safe_float(
            trade.pnl_money
        )

        if equity > peak_equity:

            peak_equity = equity

        drawdown = (
            peak_equity -
            equity
        )

        if drawdown > max_drawdown:

            max_drawdown = drawdown


        equity_values.append(
            round(equity, 2)
        )

        equity_labels.append(
            trade.date
        )


    # --------------------------------------------------------
    # LONG / SHORT
    # --------------------------------------------------------

    long_trades = [
        trade
        for trade in trades
        if trade.direction == "LONG"
    ]


    short_trades = [
        trade
        for trade in trades
        if trade.direction == "SHORT"
    ]


    long_count = len(
        long_trades
    )

    short_count = len(
        short_trades
    )


    long_pnl = sum(
        safe_float(trade.pnl_money)
        for trade in long_trades
    )


    short_pnl = sum(
        safe_float(trade.pnl_money)
        for trade in short_trades
    )


    # --------------------------------------------------------
    # COIN PERFORMANCE
    # --------------------------------------------------------

    coin_pnl = defaultdict(float)

    for trade in trades:

        ticker = (
            trade.ticker.upper()
            if trade.ticker
            else "UNKNOWN"
        )

        coin_pnl[ticker] += safe_float(
            trade.pnl_money
        )


    if coin_pnl:

        top_coin = max(
            coin_pnl,
            key=coin_pnl.get
        )

        top_coin_pnl = coin_pnl[
            top_coin
        ]

    else:

        top_coin = None

        top_coin_pnl = 0


    # --------------------------------------------------------
    # DAILY P&L
    # --------------------------------------------------------

    daily_pnl_dict = defaultdict(float)

    for trade in trades:

        daily_pnl_dict[
            trade.date
        ] += safe_float(
            trade.pnl_money
        )


    daily_labels = list(
        daily_pnl_dict.keys()
    )

    daily_pnl = [
        round(
            daily_pnl_dict[label],
            2
        )
        for label in daily_labels
    ]


    # --------------------------------------------------------
    # STRATEGY PERFORMANCE
    # --------------------------------------------------------

    strategy_pnl_dict = defaultdict(float)

    for trade in trades:

        strategy = (
            trade.strategy.strip()
            if trade.strategy
            else "No Strategy"
        )

        strategy_pnl_dict[
            strategy
        ] += safe_float(
            trade.pnl_money
        )


    strategy_labels = list(
        strategy_pnl_dict.keys()
    )

    strategy_values = [
        round(
            strategy_pnl_dict[strategy],
            2
        )
        for strategy in strategy_labels
    ]


    # --------------------------------------------------------
    # TRADE DATA FOR TEMPLATE
    # --------------------------------------------------------

    trade_data = []

    for trade in reversed(trades):

        trade_data.append({

            "id": trade.id,

            "date": trade.date,

            "day": trade.day,

            "ticker": trade.ticker,

            "direction": trade.direction,

            "result": trade.result,

            "entry": safe_float(
                trade.buying_average
            ),

            "exit": safe_float(
                trade.selling_average
            ),

            "tp": safe_float(
                trade.tp
            ),

            "sl": safe_float(
                trade.sl
            ),

            "risk": safe_float(
                trade.risk
            ),

            "strategy": trade.strategy or "",

            "reason": trade.reason or "",

            "pnl_percent": safe_float(
                trade.pnl_percent
            ),

            "pnl_money": safe_float(
                trade.pnl_money
            ),

            "screenshot": trade.screenshot

        })


    return render_template(

        "dashboard.html",

        user=user,

        trades=trade_data,

        total_trades=total_trades,

        total_pnl=total_pnl,

        winning_trades=winning_trades,

        losing_trades=losing_trades,

        breakeven_trades=breakeven_trades,

        win_rate=win_rate,

        profit_factor=profit_factor,

        avg_win=avg_win,

        avg_loss=avg_loss,

        best_trade=best_trade,

        worst_trade=worst_trade,

        avg_rr=avg_rr,

        max_drawdown=max_drawdown,

        total_risk=total_risk,

        long_count=long_count,

        short_count=short_count,

        long_pnl=long_pnl,

        short_pnl=short_pnl,

        top_coin=top_coin,

        top_coin_pnl=top_coin_pnl,

        daily_labels=daily_labels,

        daily_pnl=daily_pnl,

        equity_labels=equity_labels,

        equity_values=equity_values,

        strategy_labels=strategy_labels,

        strategy_values=strategy_values,

        search=search,

        result_filter=result_filter,

        direction_filter=direction_filter

    )


# ============================================================
# UPDATE CAPITAL
# ============================================================

@app.route(
    "/update_capital",
    methods=["POST"]
)
@login_required
def update_capital():

    user = get_current_user()

    capital = safe_float(
        request.form.get(
            "capital_usdt"
        )
    )

    user.capital_usdt = capital

    db.session.commit()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# ADD TRADE
# ============================================================

@app.route(
    "/add_trade",
    methods=["GET", "POST"]
)
@login_required
def add_trade():

    user = get_current_user()


    if request.method == "POST":

        date_value = request.form.get(
            "date"
        )

        day = request.form.get(
            "day"
        )

        ticker = request.form.get(
            "ticker",
            ""
        ).strip().upper()

        direction = request.form.get(
            "direction",
            "LONG"
        )

        result = request.form.get(
            "result",
            "LOSS"
        )

        entry = safe_float(
            request.form.get(
                "buying_average"
            )
        )

        exit_price = safe_float(
            request.form.get(
                "selling_average"
            )
        )

        tp = safe_float(
            request.form.get(
                "tp"
            )
        )

        sl = safe_float(
            request.form.get(
                "sl"
            )
        )

        risk = safe_float(
            request.form.get(
                "risk"
            )
        )

        strategy = request.form.get(
            "strategy",
            ""
        ).strip()

        reason = request.form.get(
            "reason",
            ""
        ).strip()

        notes = request.form.get(
            "notes",
            ""
        ).strip()


        capital = (
            safe_float(
                request.form.get(
                    "capital"
                )
            )
            or
            safe_float(
                user.capital_usdt
            )
        )


        calculation = calculate_trade(

            capital,

            risk,

            sl,

            tp,

            result,

            entry,

            exit_price,

            direction

        )


        screenshot_file = request.files.get(
            "screenshot"
        )

        screenshot_filename = None


        if screenshot_file and screenshot_file.filename:

            os.makedirs(
                app.config["UPLOAD_FOLDER"],
                exist_ok=True
            )

            filename = secure_filename(
                screenshot_file.filename
            )

            unique_filename = (
                datetime.now().strftime(
                    "%Y%m%d%H%M%S"
                )
                + "_"
                + filename
            )

            screenshot_file.save(
                os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    unique_filename
                )
            )

            screenshot_filename = unique_filename


        trade = Trade(

            user_id=user.id,

            date=date_value,

            day=day,

            ticker=ticker,

            direction=direction,

            result=result,

            buying_average=entry,

            selling_average=exit_price,

            tp=tp,

            sl=sl,

            risk=risk,

            reason=reason,

            strategy=strategy,

            notes=notes,

            screenshot=screenshot_filename,

            risk_amount=calculation[
                "risk_amount"
            ],

            position_size=calculation[
                "position_size"
            ],

            pnl_percent=calculation[
                "pnl_percent"
            ],

            pnl_money=calculation[
                "pnl_money"
            ]

        )


        db.session.add(trade)

        db.session.commit()


        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "add_trade.html",
        user=user
    )


# ============================================================
# EDIT TRADE
# ============================================================

@app.route(
    "/edit_trade/<int:trade_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_trade(trade_id):

    user = get_current_user()

    trade = Trade.query.filter_by(
        id=trade_id,
        user_id=user.id
    ).first_or_404()


    if request.method == "POST":

        trade.date = request.form.get(
            "date"
        )

        trade.day = request.form.get(
            "day"
        )

        trade.ticker = request.form.get(
            "ticker",
            ""
        ).strip().upper()

        trade.direction = request.form.get(
            "direction",
            "LONG"
        )

        trade.result = request.form.get(
            "result",
            "LOSS"
        )

        trade.buying_average = safe_float(
            request.form.get(
                "buying_average"
            )
        )

        trade.selling_average = safe_float(
            request.form.get(
                "selling_average"
            )
        )

        trade.tp = safe_float(
            request.form.get(
                "tp"
            )
        )

        trade.sl = safe_float(
            request.form.get(
                "sl"
            )
        )

        trade.risk = safe_float(
            request.form.get(
                "risk"
            )
        )

        trade.strategy = request.form.get(
            "strategy",
            ""
        )

        trade.reason = request.form.get(
            "reason",
            ""
        )

        trade.notes = request.form.get(
            "notes",
            ""
        )


        calculation = calculate_trade(

            user.capital_usdt,

            trade.risk,

            trade.sl,

            trade.tp,

            trade.result,

            trade.buying_average,

            trade.selling_average,

            trade.direction

        )


        trade.risk_amount = calculation[
            "risk_amount"
        ]

        trade.position_size = calculation[
            "position_size"
        ]

        trade.pnl_percent = calculation[
            "pnl_percent"
        ]

        trade.pnl_money = calculation[
            "pnl_money"
        ]


        db.session.commit()


        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "add_trade.html",
        user=user,
        trade=trade,
        edit_mode=True
    )


# ============================================================
# DELETE TRADE
# ============================================================

@app.route(
    "/delete_trade/<int:trade_id>"
)
@login_required
def delete_trade(trade_id):

    user = get_current_user()

    trade = Trade.query.filter_by(
        id=trade_id,
        user_id=user.id
    ).first_or_404()

    db.session.delete(trade)

    db.session.commit()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# CSV EXPORT
# ============================================================

@app.route(
    "/export_csv"
)
@login_required
def export_csv():

    user = get_current_user()

    trades = Trade.query.filter_by(
        user_id=user.id
    ).order_by(
        Trade.date.asc()
    ).all()


    output = io.StringIO()

    writer = csv.writer(
        output
    )


    writer.writerow([

        "Date",
        "Day",
        "Ticker",
        "Direction",
        "Entry",
        "Exit",
        "TP %",
        "SL %",
        "Risk %",
        "Risk Amount",
        "Position Size",
        "Result",
        "P&L %",
        "P&L USDT",
        "Strategy",
        "Reason",
        "Notes"

    ])


    for trade in trades:

        writer.writerow([

            trade.date,

            trade.day,

            trade.ticker,

            trade.direction,

            trade.buying_average,

            trade.selling_average,

            trade.tp,

            trade.sl,

            trade.risk,

            trade.risk_amount,

            trade.position_size,

            trade.result,

            trade.pnl_percent,

            trade.pnl_money,

            trade.strategy,

            trade.reason,

            trade.notes

        ])


    output.seek(0)


    return send_file(

        io.BytesIO(
            output.getvalue().encode(
                "utf-8"
            )
        ),

        mimetype="text/csv",

        as_attachment=True,

        download_name="trading_journal.csv"

    )


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(404)
def page_not_found(error):

    return "Page not found.", 404


# ============================================================
# CREATE DATABASE
# ============================================================

with app.app_context():

    db.create_all()

    migrate_database()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    os.makedirs(
        app.config["UPLOAD_FOLDER"],
        exist_ok=True
    )

    app.run(
        debug=True
    )