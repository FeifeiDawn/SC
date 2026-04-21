import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import math
import time
import io

# ==========================================
# 页面配置与样式
# ==========================================
st.set_page_config(page_title="T0 SKU 供应链寻优沙盘", layout="wide", page_icon="🚢")

st.markdown("""
    <style>
    .kpi-card {
        background-color: #ffffff;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
    }
    .kpi-title { color: #64748b; font-size: 14px; margin-bottom: 8px; }
    .kpi-value { color: #0f172a; font-size: 24px; font-weight: bold; }
    .kpi-desc { color: #94a3b8; font-size: 12px; margin-top: 8px; }
    .sku-info-bar {
        background-color: #f8fafc;
        padding: 12px 20px;
        border-radius: 8px;
        border: 1px solid #e2e8f0;
        margin-bottom: 20px;
        font-size: 14px;
        color: #475569;
        display: flex;
        flex-wrap: wrap;
        gap: 20px;
    }
    .sku-info-item span { color: #0f172a; font-weight: 600; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 状态初始化 & 安全数据提取工具
# ==========================================
if 'sku_data_list' not in st.session_state:
    st.session_state.sku_data_list = [] 
if 'z_slider' not in st.session_state:
    st.session_state.z_slider = 1.0
if 'a_slider' not in st.session_state:
    st.session_state.a_slider = 0.5
if 'current_sku_id' not in st.session_state:
    st.session_state.current_sku_id = None

def safe_float(val, default=0.0):
    if pd.isna(val): return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    if pd.isna(val): return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

# ==========================================
# Excel 处理引擎 (动态支持周数)
# ==========================================
def generate_excel_template():
    dummy_row = {
        'Market': 'AP', 'Channel': 'Amazon', 'SKU_ID': 'USCAF119-CAF',
        'Category': 'CAF', 'Level': 'TOP0', 'Sea_LT': 8, 'Safety_Stock': 2, 'Initial_Overseas_Stock': 5195
    }
    # 历史销量：固定 12 周 (对应 W4 - W15)
    for i in range(12): dummy_row[f'Past_Sales_W{i+1}'] = 200
    # 未来预测：生成 37 周 (对应 W16 - W52)
    for i in range(37): dummy_row[f'Forecast_W{i+1}'] = 300
    
    for i in range(4):
        dummy_row[f'Pipeline_{i+1}_Arrival_Week'] = '2026/4/13' if i == 0 else ('2026/4/27' if i == 1 else "无在途")
        dummy_row[f'Pipeline_{i+1}_Qty'] = 400 if i < 2 else "无在途"
            
    df = pd.DataFrame([dummy_row])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='SKU_Data')
    return output.getvalue()

def parse_excel_to_skus(df, t0_date):
    sku_list = []
    for _, row in df.iterrows():
        try:
            sku = {
                'market': str(row.get('Market') if 'Market' in row else row.get('市场', '未指定')),
                'channel': str(row.get('Channel') if 'Channel' in row else row.get('渠道', '未指定')),
                'id': str(row.get('SKU_ID') if 'SKU_ID' in row else row.get('MRP SKU', 'Unknown_SKU')),
                'category': str(row.get('Category') if 'Category' in row else row.get('品类', '未指定')),
                'level': str(row.get('Level') if 'Level' in row else row.get('物控层级', 'TOP0')),
                'sea_lt': safe_int(row.get('Sea_LT') if 'Sea_LT' in row else row.get('海运LT'), 8),
                'safety_stock': safe_int(row.get('Safety_Stock') if 'Safety_Stock' in row else row.get('安全库存'), 2),
                'initialOverseasStock': safe_int(row.get('Initial_Overseas_Stock') if 'Initial_Overseas_Stock' in row else row.get('MRP在库'), 0),
                'pastSales': [],
                'futureForecast': [],
                'pipeline': []
            }
            
            for i in range(1, 13):
                col_en, col_cn = f'Past_Sales_W{i}', f'W{i+3}'
                val = row.get(col_en) if col_en in row else row.get(col_cn)
                sku['pastSales'].append(safe_float(val, 0.0))
            
            # 动态探测未来预测数据的长度，并智能截断尾部空数据
            forecast_list = []
            for i in range(1, 100): # 支持最高 100 周预测跨度
                col_en, col_cn = f'Forecast_W{i}', f'W{i+15}'
                val = None
                if col_en in df.columns: val = row[col_en]
                elif col_cn in df.columns: val = row[col_cn]
                else: break
                
                if pd.isna(val) or str(val).strip() == '':
                    forecast_list.append(None)
                else:
                    forecast_list.append(safe_float(val, 0.0))
            
            # 剔除尾部纯粹的空单元格 (NaN)
            while forecast_list and forecast_list[-1] is None:
                forecast_list.pop()
            
            # 将中间夹杂的空单元格按 0.0 处理
            forecast_list = [0.0 if x is None else x for x in forecast_list]
            if not forecast_list: forecast_list = [0.0] * 12 # 兜底机制
            sku['futureForecast'] = forecast_list
                
            invalid_texts = ['无在途', 'none', 'nan', 'nat', '#value!', '#n/a', '']
            for i in range(1, 5):
                w_col_en, q_col_en = f'Pipeline_{i}_Arrival_Week', f'Pipeline_{i}_Qty'
                w_col_cn, q_col_cn = f'第{i}批上架时间', f'第{i}批在途数量'
                
                w_val = row.get(w_col_en) if w_col_en in row else row.get(w_col_cn)
                q_val = row.get(q_col_en) if q_col_en in row else row.get(q_col_cn)
                
                if pd.notna(w_val) and pd.notna(q_val):
                    w_str, q_str = str(w_val).strip().lower(), str(q_val).strip().lower()
                    if w_str not in invalid_texts and q_str not in invalid_texts:
                        qty = safe_float(q_str, 0.0)
                        if qty > 0:
                            try:
                                week_int = int(float(w_str))
                                sku['pipeline'].append({'week': week_int, 'qty': qty})
                            except ValueError:
                                try:
                                    if isinstance(w_val, (int, float)):
                                        arrival_dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(w_val, unit='D')
                                        arrival_dt = arrival_dt.date()
                                    else:
                                        arrival_dt = pd.to_datetime(w_val, errors='coerce').date()
                                    if pd.notna(arrival_dt):
                                        days_diff = (arrival_dt - t0_date).days
                                        week_int = math.ceil(days_diff / 7)
                                        if week_int <= 0: week_int = 1 
                                        sku['pipeline'].append({'week': week_int, 'qty': qty})
                                except Exception:
                                    pass 
            sku_list.append(sku)
        except Exception as e:
            st.warning(f"跳过解析失败的行 (SKU: {row.get('SKU_ID', '未知')}): {e}")
            continue
    return sku_list

# ==========================================
# 核心逻辑引擎与打分器 (动态窗口截断)
# ==========================================
def calculate_stats(past_sales, lt):
    mean = np.mean(past_sales)
    std_dev = np.std(past_sales, ddof=0)
    sigma_l_val = math.sqrt(max(0, lt))
    sigma_dl = sigma_l_val * std_dev
    return mean, std_dev, sigma_l_val, sigma_dl

def run_simulation(sku, lt, ss, moq, z_val, alpha, pen_out, pen_ss, pen_over):
    mean, std_dev, sigma_l_val, sigma_dl = calculate_stats(sku['pastSales'], lt)
    current_stock = sku['initialOverseasStock']
    active_pipeline = [p.copy() for p in sku.get('pipeline', [])]
    forecast_list = sku['futureForecast']
    
    max_future_weeks = len(forecast_list)
    future_sim = []
    score = 0

    # 动态匹配用户上传的周数
    for i in range(max_future_weeks):
        f = forecast_list[i]
        week_label = f"W{i + 16}" # T0=W16

        # 评分 Base：当前周 + 未来 3 周
        eval_slice = forecast_list[i : min(i+4, max_future_weeks)]
        if len(eval_slice) < 4: eval_slice.extend([forecast_list[-1]] * (4 - len(eval_slice)))
        eval_base = max(np.mean(eval_slice) if eval_slice else 0.0001, 0.0001)

        # 发货 Base：发货后 LT 周内，4 周的平均销量
        ship_slice = forecast_list[i+lt : min(i+lt+4, max_future_weeks)]
        if not ship_slice: ship_slice = [forecast_list[-1]] * 4
        elif len(ship_slice) < 4: ship_slice.extend([forecast_list[-1]] * (4 - len(ship_slice)))
        ship_base = max(np.mean(ship_slice), 0.0001)

        # 本周入库与扣减
        arrived = sum(p['qty'] for p in active_pipeline if p['week'] == i + 1)
        current_stock = current_stock + arrived - f

        # 计算上下边界参数
        safety_stock_line = round(ss * eval_base)
        target_level = round((ss + lt) * ship_base + z_val * sigma_dl)
        total_in_transit = sum(p['qty'] for p in active_pipeline if p['week'] > i + 1)

        # 三阶罚分逻辑
        stockout = 0
        if current_stock < 0:
            stockout = abs(current_stock)
            current_stock = 0
            score += stockout * pen_out  
        elif current_stock < safety_stock_line:
            score += (safety_stock_line - current_stock) * pen_ss  
        
        if current_stock > target_level:
            score += (current_stock - target_level) * pen_over 

        # ===============================================
        # 核心逻辑：尾部时间窗截断 (防止瞎子发货)
        # ===============================================
        # 只有当我们能够“看到”发货到港后的目标窗口时，才允许执行发货决策
        order_qty = 0
        if i < max_future_weeks - lt:
            gap = target_level - (current_stock + total_in_transit)
            if gap > 0:
                order_qty = gap * alpha
                if moq > 0: order_qty = math.ceil(order_qty / moq) * moq
                else: order_qty = round(order_qty)
                active_pipeline.append({"week": i + 1 + lt, "qty": order_qty})

        total_pipeline_qty = round(current_stock + total_in_transit)
        future_sim.append({
            "time": week_label, "period": 'Future', "forecast": round(f), "arrived": arrived,
            "simOrder": order_qty, "inventory": round(current_stock), 
            "targetLevel": target_level, "safetyStockLine": safety_stock_line,
            "totalPipeline": total_pipeline_qty, "stockout": round(stockout),
            "eval_base": eval_base, "sigma_d": std_dev, "sigma_l": sigma_l_val, "sigma_dl": sigma_dl,
            "inventory_weeks": round(current_stock) / eval_base,
            "pipeline_weeks": total_pipeline_qty / eval_base,
            "target_weeks": target_level / eval_base
        })

    return future_sim, score, max_future_weeks

def auto_optimize(sku, lt, ss, moq, pen_out, pen_ss, pen_over):
    best_score = float('inf')
    best_z, best_a = 0.0, 0.1
    for z in np.arange(0.0, 3.1, 0.2):
        for a in np.arange(0.2, 1.1, 0.1):
            _, score, _ = run_simulation(sku, lt, ss, moq, z, a, pen_out, pen_ss, pen_over)
            if score < best_score:
                best_score = score
                best_z, best_a = z, a
    return round(best_z, 1), round(best_a, 1)

def update_ai_to_state(sku, lt, ss, moq, pen_out, pen_ss, pen_over):
    best_z, best_a = auto_optimize(sku, lt, ss, moq, pen_out, pen_ss, pen_over)
    st.session_state.z_slider = best_z
    st.session_state.a_slider = best_a

# ==========================================
# UI 布局
# ==========================================
col1, col2 = st.columns([2, 1.5])
with col1:
    st.title("🚢 T0 SKU 供应链发货寻优引擎")
    st.caption("v3.1 图例解耦与时间窗智能截断版 | 自动适配数据周长，截断无效远端发货噪音，独立右侧图例")

with col2:
    t0_date = st.date_input("🗓️ 设定 T0 历史切片周一日期", value=pd.to_datetime('2026-04-06').date())
    
    uploaded_file = st.file_uploader("导入 SKU 数据 (.xlsx 或 .json)", type=['xlsx', 'json'])
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.json'):
                data = json.load(uploaded_file)
                if isinstance(data, list) and len(data) > 0 and 'id' in data[0]:
                    st.session_state.sku_data_list = data
                    st.success("JSON 数据导入成功！")
            elif uploaded_file.name.endswith('.xlsx'):
                df = pd.read_excel(uploaded_file)
                data = parse_excel_to_skus(df, t0_date)
                if data:
                    st.session_state.sku_data_list = data
                    st.success(f"Excel 解析成功！成功导入 {len(data)} 个 SKU。")
                else:
                    st.error("Excel 解析失败：请确保列名与标准模板一致。")
        except Exception as e:
            st.error(f"文件读取异常: {str(e)}")
            
    st.download_button("📊 下载标准 Excel 数据模板 (含37周预测示范)", data=generate_excel_template(), file_name="sku_data_template.xlsx")

st.divider()

if not st.session_state.sku_data_list:
    st.info("👋 欢迎使用 T0 SKU 供应链寻优沙盘！当前暂无数据。请在右上角上传您的数据文件。")
    st.stop()

# 数据准备与 SKU 切换监听
sku_options = [s['id'] for s in st.session_state.sku_data_list]
selected_sku_id = st.selectbox("📌 选择要分析的 SKU (切换SKU将自动执行AI寻优)", sku_options)
current_sku = next(s for s in st.session_state.sku_data_list if s['id'] == selected_sku_id)

default_lt = safe_int(current_sku.get('sea_lt', 8), 8)
default_ss = safe_int(current_sku.get('safety_stock', 2), 2)

st.markdown(f"""
    <div class="sku-info-bar">
        <div class="sku-info-item">🌍 市场: <span>{current_sku.get('market', '-')}</span></div>
        <div class="sku-info-item">🏪 渠道: <span>{current_sku.get('channel', '-')}</span></div>
        <div class="sku-info-item">📦 品类: <span>{current_sku.get('category', '-')}</span></div>
        <div class="sku-info-item">⭐ 层级: <span>{current_sku.get('level', '-')}</span></div>
        <div class="sku-info-item">🚢 预设海运LT: <span>{default_lt} 周</span></div>
        <div class="sku-info-item">🛡️ 预设安全库存: <span>{default_ss} 周</span></div>
    </div>
""", unsafe_allow_html=True)


left_col, right_col = st.columns([1, 3], gap="large")

with left_col:
    st.subheader("⚙️ 物理硬约束")
    lt = st.slider("海运 LT (周)", min_value=0, max_value=24, value=default_lt, step=1)
    ss = st.slider("安全库存底座 (周)", min_value=0, max_value=12, value=default_ss, step=1)
    moq = st.slider("起订量 MOQ", min_value=0, max_value=500, value=0, step=10)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    with st.expander("⚖️ 代价函数与打分界限设定", expanded=True):
        st.caption("AI 将基于【当周及未来3周平均销量】作为打分基准 (Eval Base) 计算罚分。")
        pen_out = st.number_input("🚨 跌破0：断货罚分权重 (/件)", value=5.0, step=0.5)
        pen_ss = st.number_input("⚠️ 跌破下界：安全线罚分权重 (/件)", value=1.0, step=0.1)
        pen_over = st.number_input("💰 越过上界：目标压货罚分权重 (/件)", value=0.1, step=0.05)

    if st.session_state.current_sku_id != selected_sku_id:
        st.session_state.current_sku_id = selected_sku_id
        update_ai_to_state(current_sku, lt, ss, moq, pen_out, pen_ss, pen_over)

    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("🧠 发货决策参数 (已就绪 AI 最优解)")
    z_val = st.slider("安全系数 (Z值)", min_value=0.0, max_value=3.0, step=0.1, key="z_slider")
    alpha = st.slider("平滑系数 (α)", min_value=0.1, max_value=1.0, step=0.1, key="a_slider")
    
    st.button("✨ 基于当前设定重新 AI 寻优", on_click=update_ai_to_state, args=(current_sku, lt, ss, moq, pen_out, pen_ss, pen_over), use_container_width=True, type="primary")

    # 运行推演获取完整结果及动态周数
    sim_data, total_score, max_future_weeks = run_simulation(current_sku, lt, ss, moq, z_val, alpha, pen_out, pen_ss, pen_over)
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-title">动态生命周期总罚分 (越低越好)</div>
            <div class="kpi-value" style="color: #ef4444;">{round(total_score):,} 分</div>
        </div>
    """, unsafe_allow_html=True)


with right_col:
    _, std_dev, _, _ = calculate_stats(current_sku['pastSales'], lt)
    display_forecast_mean = round(np.mean([d['forecast'] for d in sim_data])) if sim_data else 0
    stockouts = len([d for d in sim_data if d['stockout'] > 0])
    
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.markdown(f"<div class='kpi-card'><div class='kpi-title'>T0 初始在库设定</div><div class='kpi-value'>{current_sku['initialOverseasStock']:,}</div><div class='kpi-desc'>当前存量资产起点</div></div>", unsafe_allow_html=True)
    kpi2.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景未来预测均值</div><div class='kpi-value'>{display_forecast_mean:,}</div><div class='kpi-desc'>历史波动率 σ: {round(std_dev)}</div></div>", unsafe_allow_html=True)
    kpi3.markdown(f"<div class='kpi-card'><div class='kpi-title'>推演总断货次数</div><div class='kpi-value' style='color:#ef4444;'>{stockouts} 周</div><div class='kpi-desc'>管线生命周期内总计</div></div>", unsafe_allow_html=True)
    kpi4.markdown(f"<div class='kpi-card'><div class='kpi-title'>最终期末推演在库</div><div class='kpi-value' style='color:#3b82f6;'>{sim_data[-1]['inventory'] if sim_data else 0:,}</div><div class='kpi-desc'>视窗终点推演水位</div></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 拼装全量数据：12周历史 + 动态探测出的未来周数
    history_df = pd.DataFrame([{
        "time": f"W{i + 4}", "actualSales": current_sku['pastSales'][i], "forecast": 0, "arrived": 0, 
        "simOrder": 0, "inventory": 0, "targetLevel": 0, "safetyStockLine": 0, "totalPipeline": 0, "stockout": 0,
        "eval_base": max(current_sku['pastSales'][i], 0.0001), "sigma_d": 0, "sigma_l": 0, "sigma_dl": 0,
        "inventory_weeks": 0, "pipeline_weeks": 0, "target_weeks": 0
    } for i in range(12)])
    future_df = pd.DataFrame(sim_data)
    full_df = pd.concat([history_df, future_df], ignore_index=True)

    for col in ['time', 'actualSales', 'forecast', 'arrived', 'simOrder', 'inventory', 'targetLevel', 'safetyStockLine', 'totalPipeline', 'stockout', 'eval_base', 'sigma_d', 'sigma_l', 'sigma_dl', 'inventory_weeks', 'pipeline_weeks', 'target_weeks']:
        if col not in full_df.columns: full_df[col] = 0

    # ==========================================
    # 数据截断：分离 Chart A 和 Chart B 的视窗
    # ==========================================
    # Chart A (发货图)：只显示到最后一次有效的发货决策 (总长 - LT)
    valid_order_len = max(0, max_future_weeks - lt)
    df_chart_a = full_df.iloc[: 12 + valid_order_len]
    
    # Chart B (水位图)：显示全量到港生命周期
    df_chart_b = full_df

    # -----------------------------
    # 图表 A：供需与发货动作流 (独立图表，右侧独立图例)
    # -----------------------------
    st.markdown("#### 📊 供需与发货动作流 (已自动剔除尾端无效发货盲区)")
    fig1 = go.Figure()
    
    fig1.add_trace(go.Bar(x=df_chart_a['time'], y=df_chart_a.get('simOrder'), name='策略生成出库单', marker_color='#8b5cf6'))
    fig1.add_trace(go.Bar(x=df_chart_a['time'], y=df_chart_a.get('arrived'), name='历史/在途到港', marker_color='#10b981'))
    fig1.add_trace(go.Scatter(x=df_chart_a['time'], y=df_chart_a.get('actualSales'), mode='lines+markers', name='历史实际销量', line=dict(color='#3b82f6', width=3)))
    fig1.add_trace(go.Scatter(x=df_chart_a['time'], y=df_chart_a.get('forecast'), mode='lines', name='未来预测销量', line=dict(color='#f97316', width=3, dash='dash')))
    
    stockout_df_a = df_chart_a[df_chart_a['stockout'] > 0]
    if not stockout_df_a.empty:
        fig1.add_trace(go.Scatter(x=stockout_df_a['time'], y=stockout_df_a['forecast'], mode='markers', name='🚨 断货预警点', marker=dict(color='red', size=12, symbol='x', line=dict(width=2, color='darkred'))))

    fig1.add_vline(x=11.5, line_dash="dash", line_color="#94a3b8", annotation_text="T0 历史切片", annotation_position="top left")
    
    fig1.update_layout(
        height=350, 
        margin=dict(l=0, r=180, t=30, b=0), # 增加右侧边距容纳图例
        plot_bgcolor="white", 
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02), # 图例垂直放于右侧
        hovermode="x unified"
    )
    fig1.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')
    fig1.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')
    
    st.plotly_chart(fig1, use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # -----------------------------
    # 图表 B：高级运筹学水位渲染 (独立图表，右侧独立图例)
    # -----------------------------
    st.markdown("#### 🛡️ 运筹水位沙盘：自适应目标区间与管线健康度监控")
    fig2 = go.Figure()
    
    fig2.add_trace(go.Scatter(x=df_chart_b['time'], y=df_chart_b.get('safetyStockLine'), mode='lines', line_shape='hv', name='打分下界 (安全库存线)', line=dict(color='#ef4444', width=2, dash='dot')))
    
    c_data_target = np.column_stack((df_chart_b.get('sigma_d'), df_chart_b.get('sigma_l'), df_chart_b.get('sigma_dl'), df_chart_b.get('target_weeks')))
    fig2.add_trace(go.Scatter(
        x=df_chart_b['time'], y=df_chart_b.get('targetLevel'), 
        mode='lines', line_shape='hv', name='打分上界 (自适应目标水位)', 
        line=dict(color='#eab308', width=2), fill='tonexty', fillcolor='rgba(134, 239, 172, 0.2)',
        customdata=c_data_target,
        hovertemplate="自适应水位: %{y:.0f}件 (%{customdata[3]:.1f}周)<br>--- 内核透视 ---<br>销量波动(σD): %{customdata[0]:.1f}件<br>物流波动(σL): %{customdata[1]:.1f}周<br>联合风险缺口: %{customdata[2]:.1f}件"
    ))
    
    fig2.add_trace(go.Scatter(
        x=df_chart_b['time'], y=df_chart_b.get('totalPipeline'), 
        mode='lines', line_shape='hv', name='总资产管线 (在库+在途)', 
        line=dict(color='#93c5fd', width=2, dash='dash'),
        customdata=df_chart_b.get('pipeline_weeks'),
        hovertemplate="管线总计: %{y:.0f}件 (%{customdata:.1f}周)"
    ))

    fig2.add_trace(go.Scatter(
        x=df_chart_b['time'], y=df_chart_b.get('inventory'), 
        mode='lines+markers', name='推演期末在库', 
        line=dict(color='#0ea5e9', width=4), marker=dict(size=6, color='white', line=dict(width=2, color='#0ea5e9')),
        customdata=df_chart_b.get('inventory_weeks'),
        hovertemplate="在库: %{y:.0f}件 (%{customdata:.1f}周)"
    ))

    fig2.add_vline(x=11.5, line_dash="dash", line_color="#94a3b8", annotation_text="T0 历史切片", annotation_position="top left")
    
    fig2.update_layout(
        height=350, 
        margin=dict(l=0, r=180, t=30, b=0), # 同步图表 A 的缩进比例，对齐右侧图例
        plot_bgcolor="white", 
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02), # 图例垂直放于右侧
        hovermode="x unified"
    )
    fig2.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')
    fig2.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')
    
    st.plotly_chart(fig2, use_container_width=True)