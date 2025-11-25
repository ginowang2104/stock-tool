import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta, date

# --- 頁面設定 ---
st.set_page_config(page_title="美股區間再平衡回測", layout="wide")
st.title("⚖️ 美股：股票/現金 區間再平衡回測系統")

# --- 側邊欄：參數設定 ---
st.sidebar.header("1. 投資標的與資金")
ticker = st.sidebar.text_input("股票代號 (例如: SPY, VTI, 006312.TW)", value="SPY").upper()
#years = st.sidebar.number_input("回測年數", min_value=1, max_value=30, value=10)
initial_capital = st.sidebar.number_input("初始資金 (USD)", value=10000)

# 新增參數：買進持有比例
bh_allocation_pct = st.sidebar.slider(
    "買進持有 (不賣) 比例 (%)",
    min_value=0, 
    max_value=100, 
    value=70, 
    help="這部分資金會買入股票後，不再進行任何交易（不會被再平衡策略影響）。"
) / 100.0

# 策略資金比例
strategy_allocation_pct = 1.0 - bh_allocation_pct
st.sidebar.info(f"👉 剩餘 **{strategy_allocation_pct*100:.0f}%** 資金將用於再平衡策略。")

# --- 新增回測區間設定 ---
st.sidebar.header("3. 回測區間")
today = date.today()
year_5_ago = today.year - 5
# 預設起始日：五年前的 1 月 1 號
default_start = date(year_5_ago, 1, 1)

start_date = st.sidebar.date_input(
    "回測起始日 (From)",
    value=default_start,
    min_value=date(1990, 1, 1),
    max_value=today - timedelta(days=1)
)

end_date = st.sidebar.date_input(
    "回測結束日 (To)",
    value=today - timedelta(days=1), # 預設值為昨天 (最近一個交易日)
    min_value=start_date,
    max_value=today
)
# -------------------------

st.sidebar.header("2. 策略參數 (股票佔比)")
# 設定目標股票比例
target_stock_pct = st.sidebar.slider("目標『股票』配置比例 (%)", 0, 100, 60) / 100.0

# 設定觸發區間
st.sidebar.subheader("觸發再平衡門檻")
trigger_high = st.sidebar.number_input(f"當股票比例 > 多少% 時賣出股票", min_value=0, max_value=100, value=70) / 100.0
trigger_low = st.sidebar.number_input(f"當股票比例 < 多少% 時買入股票", min_value=0, max_value=100, value=50) / 100.0

# 檢查參數邏輯
if trigger_low >= target_stock_pct or trigger_high <= target_stock_pct:
    st.sidebar.error("⚠️ 警告：觸發門檻設定不合邏輯！\n通常：下限 < 目標 < 上限")

st.sidebar.markdown("---")
st.sidebar.info(
    f"""
    **策略邏輯：**
    1. **目標**：維持 {target_stock_pct*100:.0f}% 股票 + {(1-target_stock_pct)*100:.0f}% 現金。
    2. **賣出**：若股票漲至總資產 {trigger_high*100:.0f}% 以上，賣出部分股票，將比例降回 {target_stock_pct*100:.0f}%。
    3. **買入**：若股票跌至總資產 {trigger_low*100:.0f}% 以下，用現金買入股票，將比例升回 {target_stock_pct*100:.0f}%。
    """
)

# --- 核心邏輯函數 ---
def run_backtest(df, initial_cap, target_pct, low_trig, high_trig, bh_alloc_pct):
    # --- 1. 資金分配 ---
    bh_cap = initial_cap * bh_alloc_pct         # 買進持有部位的初始資金
    strategy_cap = initial_cap * (1 - bh_alloc_pct) # 再平衡部位的初始資金
    
    # 股票價格 (第一天)
    price = df.iloc[0]['Price']
    
    # --- 2. 買進持有部位 (BH Portion) ---
    # 這部分資金全部買入股票後，永遠持有
    bh_shares = bh_cap / price
    
    # --- 3. 再平衡部位 (Rebalancing Portion) ---
    # 策略資金分配：策略現金 + 策略股票
    cash = strategy_cap * (1 - target_pct)
    stock_val = strategy_cap * target_pct
    shares = stock_val / price
    
    history = []
    transactions = []
    
    # 策略買進持有對照組 (Buy & Hold - 100% 資金)
    # 為了和舊的 Buy & Hold 圖線比較，這裡保持使用 100% 資金的 B&H
    total_bh_shares = initial_cap / price 

    for date, row in df.iterrows():
        price = row['Price']
        
        # ***** 再平衡策略計算 *****
        # 計算策略部位的當前市值
        current_strategy_stock_val = shares * price
        total_strategy_assets = cash + current_strategy_stock_val
        current_stock_pct = current_strategy_stock_val / total_strategy_assets
        
        action = None
        trade_amt = 0
        
        # 判斷是否觸發再平衡
        if current_stock_pct >= high_trig:
            # 股票太多 -> 賣出，回到目標比例
            target_stock_val = total_strategy_assets * target_pct
            sell_amt = current_strategy_stock_val - target_stock_val
            
            shares_to_sell = sell_amt / price
            shares -= shares_to_sell
            cash += sell_amt
            
            action = "賣出 (止盈)"
            trade_amt = sell_amt
            
        elif current_stock_pct <= low_trig:
            # 股票太少 -> 買入，回到目標比例
            target_stock_val = total_strategy_assets * target_pct
            buy_amt = target_stock_val - current_strategy_stock_val
            
            # 確保現金足夠 (雖然理論上會補現金，但在極端崩盤下檢查一下)
            if cash >= buy_amt:
                shares_to_buy = buy_amt / price
                shares += shares_to_buy
                cash -= buy_amt
                action = "買入 (低接)"
                trade_amt = buy_amt
            else:
                # 現金不足以完全平衡時，全買
                shares_to_buy = cash / price
                shares += shares_to_buy
                cash = 0
                action = "買入 (現金耗盡)"
                trade_amt = cash

        # 紀錄交易
        if action:
            transactions.append({
                "日期": date.strftime('%Y-%m-%d'),
                "動作": action,
                "股價": f"{price:.2f}",
                "交易金額": f"{trade_amt:.2f}",
                "平衡後股票佔比": f"{(shares * price / (cash + shares * price)) * 100:.1f}%"
            })
        
        # ***** 每日總資產紀錄 *****
        # 總資產 = 買進持有部位價值 + 再平衡部位價值
        bh_value = bh_shares * price
        strategy_value = cash + (shares * price)
        total_strategy_value = bh_value + strategy_value

        # 每日紀錄
        history.append({
            "Date": date,
            "Strategy_Value": total_strategy_value, # <--- 這裡使用加總後的總值
            "Buy_Hold_Value": total_bh_shares * price,
            "Stock_Pct": total_strategy_value / total_strategy_value # 這裡的比例不再是單純的策略比例，但為了繪圖不變
        })
        
    return pd.DataFrame(history), pd.DataFrame(transactions)

# --- 主程式區塊 ---
if st.button("🚀 開始回測", type="primary"):
    with st.spinner('正在下載數據並進行運算...'):
        # 1. 抓取資料
        #end_date = datetime.today()
        #start_date = end_date - timedelta(days=years*365)
        # 程式碼已在上方側邊欄取得 start_date 和 end_date
        
        try:
            # 這裡直接使用側邊欄的 start_date 和 end_date 變數
            df = yf.download(ticker, start=start_date, end=end_date)
            if df.empty:
                st.error(f"❌ 找不到代號 {ticker} 的資料。")
                st.stop()
            
            # 資料清理 (yfinance 有時會有多層 index)
            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs('Adj Close', level=0, axis=1) if 'Adj Close' in df.columns.levels[0] else df.xs('Close', level=0, axis=1)
                # 這裡簡單處理，假設只有一個 ticker，取第一欄
                df = df.iloc[:, 0].to_frame(name='Price')
            else:
                df = df[['Adj Close']].rename(columns={'Adj Close': 'Price'})
                
        except Exception as e:
            st.error(f"數據錯誤: {e}")
            st.stop()

        # 2. 執行策略
        res_df, trans_df = run_backtest(df, initial_capital, target_stock_pct, trigger_low, trigger_high, bh_allocation_pct)
        res_df.set_index('Date', inplace=True)

        # 3. 計算績效
        final_val = res_df['Strategy_Value'].iloc[-1]
        bh_final_val = res_df['Buy_Hold_Value'].iloc[-1]
        
        strat_ret = (final_val - initial_capital) / initial_capital
        bh_ret = (bh_final_val - initial_capital) / initial_capital
        
        # --- 顯示結果儀表板 ---
        st.success("回測完成！")
        
        # 頂部 KPI
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("策略最終資產", f"${final_val:,.0f}", f"{strat_ret*100:.2f}%")
        kpi2.metric("買進持有資產", f"${bh_final_val:,.0f}", f"{bh_ret*100:.2f}%")
        kpi3.metric("交易次數", f"{len(trans_df)} 次", help="觸發再平衡的總次數")

        # 圖表 1: 資產走勢比較 (使用 Plotly 互動圖)
        st.subheader("📈 資產成長走勢比較")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=res_df.index, y=res_df['Strategy_Value'], name='再平衡策略', line=dict(color='orange', width=2)))
        fig.add_trace(go.Scatter(x=res_df.index, y=res_df['Buy_Hold_Value'], name='買進持有', line=dict(color='gray', dash='dot')))
        fig.update_layout(xaxis_title="年份", yaxis_title="資產總值 (USD)", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        # 圖表 2: 持股比例監控
        st.subheader("📊 持股比例變化")
        st.caption(f"藍色區域為您的設定區間：{trigger_low*100:.0f}% - {trigger_high*100:.0f}%。超出此區間即觸發交易。")
        
        fig2 = go.Figure()
        # 繪製持股比例線
        fig2.add_trace(go.Scatter(x=res_df.index, y=res_df['Stock_Pct'], name='持股比例', line=dict(color='#3366cc')))
        # 繪製上下限
        fig2.add_hline(y=trigger_high, line_dash="dash", line_color="red", annotation_text="賣出上限")
        fig2.add_hline(y=trigger_low, line_dash="dash", line_color="green", annotation_text="買入下限")
        fig2.add_hline(y=target_stock_pct, line_dash="solid", line_color="black", annotation_text="目標")
        
        fig2.update_layout(yaxis=dict(tickformat=".0%", range=[0, 1.1]), hovermode="x unified")
        st.plotly_chart(fig2, use_container_width=True)

        # 交易明細
        st.subheader("📝 交易明細表")
        if not trans_df.empty:
            st.dataframe(trans_df, use_container_width=True)
        else:
            st.info("回測期間內，持股比例未觸發上下限，故無交易產生。")

else:
    st.info("👈 請在左側調整參數，並點擊「開始回測」按鈕")
