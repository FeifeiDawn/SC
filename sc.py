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
st.set_page_config(page_title="T0 SKU 供应链协同寻优沙盘", layout="wide", page_icon="🚢")

st.markdown("""
    <style>
    .kpi-card {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
        margin-bottom: 10px;
    }
    .kpi-title { color: #64748b; font-size: 13px; margin-bottom: 4px; }
    .kpi-value { color: #0f172a; font-size: 20px; font-weight: bold; }
    .kpi-desc { color: #94a3b8; font-size: 11px; margin-top: 4px; }
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
    .stDataFrame { margin-bottom: -15px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 状态初始化 & 安全数据提取工具
# ==========================================
if 'sku_data_list' not in st.session_state: st.session_state.sku_data_list = [] 
if 'z_base' not in st.session_state: st.session_state.z_base = 1.0
if 'a_base' not in st.session_state: st.session_state.a_base = 0.5
if 'z_hyb' not in st.session_state: st.session_state.z_hyb = 1.0
if 'a_hyb' not in st.session_state: st.session_state.a_hyb = 0.5
if 'manual_overrides' not in st.session_state: st.session_state.manual_overrides = {} 
if 'current_sku_id' not in st.session_state: st.session_state.current_sku_id = None
if 'last_uploaded_file' not in st.session_state: st.session_state.last_uploaded_file = None 

# 全局参数状态
if 'global_lt' not in st.session_state: st.session_state.global_lt = 10
if 'global_ss' not in st.session_state: st.session_state.global_ss = 2
if 'global_review_period' not in st.session_state: st.session_state.global_review_period = 1
if 'global_offset' not in st.session_state: st.session_state.global_offset = 0
if 'global_moq' not in st.session_state: st.session_state.global_moq = 0
if 'global_pen_out' not in st.session_state: st.session_state.global_pen_out = 5.0
if 'global_pen_ss' not in st.session_state: st.session_state.global_pen_ss = 1.0
if 'global_pen_over' not in st.session_state: st.session_state.global_pen_over = 0.1
if 'global_discount' not in st.session_state: st.session_state.global_discount = 0.95

# 排序缓存与触发器
if 'sku_scores_cache' not in st.session_state: st.session_state.sku_scores_cache = {}
if 'last_global_params' not in st.session_state: st.session_state.last_global_params = None
if 'force_recompute_scores' not in st.session_state: st.session_state.force_recompute_scores = False

def safe_float(val, default=0.0):
    if pd.isna(val): return default
    try: return float(val)
    except (ValueError, TypeError): return default

def safe_int(val, default=0):
    if pd.isna(val): return default
    try: return int(float(val))
    except (ValueError, TypeError): return default

# ==========================================
# Excel 处理引擎
# ==========================================
def generate_excel_template():
    dummy_row = {
        'Market': 'AP', 'Channel': 'Amazon', 'SKU_ID': 'USCAF119-CAF',
        'Category': 'CAF', 'Level': 'TOP0'
    }
    for i in range(12): dummy_row[f'Past_Sales_W{i+1}'] = 200
    for i in range(35): dummy_row[f'Forecast_W{i+1}'] = 300 
    
    for i in range(4):
        dummy_row[f'Pipeline_{i+1}_Arrival_Week'] = '2026/4/20' if i == 0 else ('2026/5/4' if i == 1 else "无在途")
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
                'initialOverseasStock': safe_int(row.get('Initial_Overseas_Stock') if 'Initial_Overseas_Stock' in row else row.get('MRP在库'), 0),
                'pastSales': [],
                'futureForecast': [],
                'pipeline': []
            }
            for i in range(1, 13):
                col_en, col_cn = f'Past_Sales_W{i}', f'W{i+3}'
                val = row.get(col_en) if col_en in row else row.get(col_cn)
                sku['pastSales'].append(safe_float(val, 0.0))
            
            forecast_list = []
            for i in range(1, 100):
                col_en, col_cn = f'Forecast_W{i}', f'W{i+15}'
                val = None
                if col_en in df.columns: val = row[col_en]
                elif col_cn in df.columns: val = row[col_cn]
                else: break
                
                if pd.isna(val) or str(val).strip() == '':
                    forecast_list.append(None)
                else:
                    forecast_list.append(safe_float(val, 0.0))
            
            while forecast_list and forecast_list[-1] is None: forecast_list.pop()
            forecast_list = [0.0 if x is None else x for x in forecast_list]
            if not forecast_list: forecast_list = [0.0] * 12 
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
            st.warning(f"跳过解析失败的行: {e}")
            continue
    return sku_list

# ==========================================
# 核心逻辑引擎：1. AI 自适应寻优发货模型
# ==========================================
def calculate_stats(past_sales, lt):
    rolling_window_size = 12
    valid_past_sales = past_sales[-rolling_window_size:] if len(past_sales) >= rolling_window_size else past_sales
    mean = np.mean(valid_past_sales) if valid_past_sales else 0
    std_dev = np.std(valid_past_sales, ddof=0) if valid_past_sales else 0
    sqrt_lt_multiplier = math.sqrt(max(0, lt))
    sigma_dl = sqrt_lt_multiplier * std_dev
    return mean, std_dev, sqrt_lt_multiplier, sigma_dl

def run_simulation(sku, lt, ss, moq, z_val, alpha, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=None):
    if overrides is None: overrides = {}
    mean, std_dev, sqrt_lt_multiplier, sigma_dl = calculate_stats(sku['pastSales'], lt)
    
    current_stock = sku['initialOverseasStock']
    active_pipeline = [p.copy() for p in sku.get('pipeline', [])]
    
    original_forecast = sku['futureForecast']
    original_weeks = len(original_forecast)
    extension_weeks = 12 + lt
    last_val = original_forecast[-1] if original_forecast else 0
    forecast_list = original_forecast + [last_val] * extension_weeks
    
    max_future_weeks = len(forecast_list)
    future_sim = []
    score = 0

    for i in range(max_future_weeks):
        f = forecast_list[i]
        week_label = f"W{i + 16}"
        weight = math.pow(discount_factor, i)

        eval_slice = forecast_list[i : min(i+4, max_future_weeks)]
        if len(eval_slice) < 4: eval_slice.extend([forecast_list[-1]] * (4 - len(eval_slice)))
        eval_base = max(np.mean(eval_slice) if eval_slice else 0.0001, 0.0001)

        ship_slice = forecast_list[i+lt : min(i+lt+4, max_future_weeks)]
        if not ship_slice: ship_slice = [forecast_list[-1]] * 4
        elif len(ship_slice) < 4: ship_slice.extend([forecast_list[-1]] * (4 - len(ship_slice)))
        ship_base = max(np.mean(ship_slice), 0.0001)

        arrived = sum(p['qty'] for p in active_pipeline if p['week'] == i + 1)
        current_stock = current_stock + arrived - f

        # 核心累加：前置期真实消耗预测
        lt_demand = sum(forecast_list[i : min(i + lt + review_period - 1, len(forecast_list))])
        safety_stock_line = round(ss * eval_base)
        target_level = round(lt_demand + ss * ship_base + z_val * sigma_dl)
        total_in_transit = sum(p['qty'] for p in active_pipeline if p['week'] > i + 1)

        stockout = 0
        week_score = 0
        if current_stock < 0:
            stockout = abs(current_stock)
            current_stock = 0
            week_score += (stockout * pen_out) * weight
        elif current_stock < safety_stock_line:
            week_score += ((safety_stock_line - current_stock) * pen_ss) * weight
        
        if current_stock > target_level:
            week_score += ((current_stock - target_level) * pen_over) * weight

        if i < original_weeks:
            score += week_score

        order_qty = 0
        is_manual = False
        is_delivery_week = (i - offset) % review_period == 0
        
        if i in overrides and overrides[i] is not None:
            order_qty = float(overrides[i])
            is_manual = True
            active_pipeline.append({"week": i + 1 + lt, "qty": order_qty})
        elif is_delivery_week and (i < max_future_weeks - lt):
            gap = target_level - (current_stock + total_in_transit)
            if gap > 0:
                order_qty = gap * alpha
                if moq > 0: order_qty = math.ceil(order_qty / moq) * moq
                else: order_qty = round(order_qty)
                active_pipeline.append({"week": i + 1 + lt, "qty": order_qty})

        total_pipeline_qty = round(current_stock + total_in_transit)
        future_sim.append({
            "index": i, "time": week_label, "period": 'Future', "forecast": round(f), "arrived": arrived,
            "simOrder": order_qty, "isManual": is_manual, "inventory": round(current_stock), 
            "targetLevel": target_level, "safetyStockLine": safety_stock_line,
            "totalPipeline": total_pipeline_qty, "stockout": round(stockout),
            "eval_base": eval_base, "sigma_d": std_dev, "sqrt_lt": sqrt_lt_multiplier, "sigma_dl": sigma_dl,
            "inventory_weeks": round(current_stock) / eval_base,
            "pipeline_weeks": total_pipeline_qty / eval_base,
            "target_weeks": target_level / eval_base
        })

    valid_sim = future_sim[:original_weeks]
    total_order_qty = sum(item.get('simOrder', 0) for item in valid_sim)
    return valid_sim, score, original_weeks, total_order_qty, std_dev

def auto_optimize(sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=None):
    best_score = float('inf')
    best_qty = float('inf')
    best_z, best_a = 0.0, 0.1
    
    # 【重构点】：拓宽 Z 值寻优上限至 3.5，并细化步长至 0.1，实现全景精密扫描
    for z in np.arange(0.0, 3.6, 0.1):
        for a in np.arange(0.1, 1.1, 0.1):
            _, score, _, total_qty, _ = run_simulation(sku, lt, ss, moq, z, a, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides)
            if score < best_score - 1e-4:
                best_score = score; best_qty = total_qty; best_z, best_a = z, a
            elif abs(score - best_score) <= 1e-4:
                if total_qty < best_qty:
                    best_qty = total_qty; best_z, best_a = z, a
    return round(best_z, 1), round(best_a, 1)

def restore_ai_optimal(sku_id):
    st.session_state.manual_overrides = {}
    cached_data = st.session_state.sku_scores_cache.get(sku_id, {})
    bz = cached_data.get('best_z', 1.0)
    ba = cached_data.get('best_a', 0.5)
    st.session_state.z_base, st.session_state.a_base = bz, ba
    st.session_state.z_slider, st.session_state.a_slider = bz, ba
    st.session_state.z_hyb, st.session_state.a_hyb = bz, ba

def update_all_ai(sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor):
    st.session_state.manual_overrides = {}
    bz, ba = auto_optimize(sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=None)
    st.session_state.z_base = bz
    st.session_state.a_base = ba
    st.session_state.z_slider = bz
    st.session_state.a_slider = ba
    st.session_state.z_hyb = bz
    st.session_state.a_hyb = ba

def reoptimize_hybrid(sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor):
    hz, ha = auto_optimize(sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=st.session_state.manual_overrides)
    st.session_state.z_hyb = hz
    st.session_state.a_hyb = ha

# ==========================================
# 核心逻辑引擎：2. 传统实际发货模型
# ==========================================
def run_legacy_simulation(sku, lt, ss, moq, pen_out, pen_ss, pen_over, discount_factor, z_base_ref):
    mean, std_dev, sqrt_lt_multiplier, sigma_dl = calculate_stats(sku['pastSales'], lt)
    current_stock = sku['initialOverseasStock']
    active_pipeline = [p.copy() for p in sku.get('pipeline', [])]
    
    original_forecast = sku['futureForecast']
    original_weeks = len(original_forecast)
    extension_weeks = 12 + lt
    last_val = original_forecast[-1] if original_forecast else 0
    forecast_list = original_forecast + [last_val] * extension_weeks
    
    max_future_weeks = len(forecast_list)
    legacy_sim = []
    score = 0
    L_window = lt + ss 

    for i in range(max_future_weeks):
        f = forecast_list[i]
        week_label = f"W{i + 16}"
        weight = math.pow(discount_factor, i)

        eval_slice = forecast_list[i : min(i+4, max_future_weeks)]
        if len(eval_slice) < 4: eval_slice.extend([forecast_list[-1]] * (4 - len(eval_slice)))
        eval_base = max(np.mean(eval_slice) if eval_slice else 0.0001, 0.0001)

        ship_slice = forecast_list[i+lt : min(i+lt+4, max_future_weeks)]
        if not ship_slice: ship_slice = [forecast_list[-1]] * 4
        elif len(ship_slice) < 4: ship_slice.extend([forecast_list[-1]] * (4 - len(ship_slice)))
        ship_base = max(np.mean(ship_slice), 0.0001)

        arrived = sum(p['qty'] for p in active_pipeline if p['week'] == i + 1)
        current_stock = current_stock + arrived - f

        lt_demand = sum(forecast_list[i : min(i + lt, len(forecast_list))])
        safety_stock_line = round(ss * eval_base)
        target_level_ref = round(lt_demand + ss * ship_base + z_base_ref * sigma_dl)
        total_in_transit = sum(p['qty'] for p in active_pipeline if p['week'] > i + 1)

        week_score = 0
        stockout = 0
        if current_stock < 0:
            stockout = abs(current_stock)
            current_stock = 0
            week_score += (stockout * pen_out) * weight
        elif current_stock < safety_stock_line:
            week_score += ((safety_stock_line - current_stock) * pen_ss) * weight
        
        if current_stock > target_level_ref:
            week_score += ((current_stock - target_level_ref) * pen_over) * weight

        if i < original_weeks:
            score += week_score

        order_qty = 0
        is_delivery_week = True 
        
        if is_delivery_week and (i < max_future_weeks - lt):
            def get_proj_gap(target_w):
                if target_w < i: return 0
                proj = current_stock 
                for k in range(i + 1, target_w + 1):
                    arr = sum(p['qty'] for p in active_pipeline if p['week'] == k + 1)
                    fcst = forecast_list[k] if k < len(forecast_list) else (forecast_list[-1] if forecast_list else 0)
                    proj = proj + arr - fcst
                return max(0, -proj)

            gap_m2 = get_proj_gap(i + L_window - 3) 
            gap_m1 = get_proj_gap(i + L_window - 2) 
            gap_0  = get_proj_gap(i + L_window - 1) 
            gap_p1 = get_proj_gap(i + L_window)     

            if gap_m2 > 0:
                order_qty = gap_m2 + gap_m1
            elif gap_m1 > 0:
                order_qty = gap_m1 + gap_0
            elif gap_0 > 0:
                order_qty = gap_0 + gap_p1
                
            if moq > 0 and order_qty > 0:
                order_qty = math.ceil(order_qty / moq) * moq
            elif order_qty > 0:
                order_qty = round(order_qty)
                
            if order_qty > 0:
                active_pipeline.append({"week": i + 1 + lt, "qty": order_qty})

        total_pipeline_qty = round(current_stock + total_in_transit)
        legacy_sim.append({
            "index": i, "time": week_label, "period": 'Future', "forecast": round(f), "arrived": arrived,
            "simOrder": order_qty, "isManual": False, "inventory": round(current_stock), 
            "targetLevel": target_level_ref, "safetyStockLine": safety_stock_line,
            "totalPipeline": total_pipeline_qty, "stockout": round(stockout),
            "eval_base": eval_base, "sigma_d": std_dev, "sqrt_lt": sqrt_lt_multiplier, "sigma_dl": sigma_dl,
            "inventory_weeks": round(current_stock) / eval_base,
            "pipeline_weeks": total_pipeline_qty / eval_base,
            "target_weeks": target_level_ref / eval_base
        })

    valid_legacy_sim = legacy_sim[:original_weeks]
    total_order_qty = sum(item.get('simOrder', 0) for item in valid_legacy_sim)
    return valid_legacy_sim, score, total_order_qty

# ==========================================
# 绘图辅助函数
# ==========================================
def build_charts(sim_data_to_use, max_fw, current_lt, mode='ai'):
    history_df = pd.DataFrame([{
        "time": f"W{i + 4}", "actualSales": current_sku['pastSales'][i], "forecast": 0, "arrived": 0, 
        "simOrder": 0, "isManual": False, "inventory": 0, "targetLevel": 0, "safetyStockLine": 0, "totalPipeline": 0, "stockout": 0,
        "eval_base": max(current_sku['pastSales'][i], 0.0001), "sigma_d": 0, "sqrt_lt": 0, "sigma_dl": 0,
        "inventory_weeks": 0, "pipeline_weeks": 0, "target_weeks": 0
    } for i in range(12)])
    future_df = pd.DataFrame(sim_data_to_use)
    full_df = pd.concat([history_df, future_df], ignore_index=True)
    
    for col in ['time', 'actualSales', 'forecast', 'arrived', 'simOrder', 'isManual', 'inventory', 'targetLevel', 'safetyStockLine', 'totalPipeline', 'stockout', 'eval_base', 'sigma_d', 'sqrt_lt', 'sigma_dl', 'inventory_weeks', 'pipeline_weeks', 'target_weeks']:
        if col not in full_df.columns: full_df[col] = 0

    valid_order_len = max(0, max_fw - current_lt)
    df_a = full_df.iloc[: 12 + valid_order_len]
    df_b = full_df

    fig1 = go.Figure()
    
    if mode == 'hybrid':
        ai_orders = [qty if not m else 0 for qty, m in zip(df_a.get('simOrder'), df_a.get('isManual'))]
        manual_orders = [qty if m else 0 for qty, m in zip(df_a.get('simOrder'), df_a.get('isManual'))]
        fig1.add_trace(go.Bar(x=df_a['time'], y=ai_orders, name='AI 建议出库', marker_color='#8b5cf6'))
        fig1.add_trace(go.Bar(x=df_a['time'], y=manual_orders, name='人工干预出库', marker_color='#f97316'))
    elif mode == 'legacy':
        fig1.add_trace(go.Bar(x=df_a['time'], y=df_a.get('simOrder'), name='传统规则出库', marker_color='#ec4899'))
    else:
        fig1.add_trace(go.Bar(x=df_a['time'], y=df_a.get('simOrder'), name='AI 建议出库', marker_color='#8b5cf6'))
        
    fig1.add_trace(go.Bar(x=df_a['time'], y=df_a.get('arrived'), name='历史/在途到港', marker_color='#10b981'))
    fig1.add_trace(go.Scatter(x=df_a['time'], y=df_a.get('actualSales'), mode='lines+markers', name='历史销量', line=dict(color='#3b82f6', width=3)))
    fig1.add_trace(go.Scatter(x=df_a['time'], y=df_a.get('forecast'), mode='lines', name='未来预测', line=dict(color='#f97316', width=3, dash='dash')))
    
    stockout_df = df_a[df_a['stockout'] > 0]
    if not stockout_df.empty: fig1.add_trace(go.Scatter(x=stockout_df['time'], y=stockout_df['forecast'], mode='markers', name='🚨 断货预警', marker=dict(color='red', size=12, symbol='x')))

    fig1.add_vline(x=11.5, line_dash="dash", line_color="#94a3b8", annotation_text="T0", annotation_position="top left")
    fig1.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), plot_bgcolor="white", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified")
    fig1.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9'); fig1.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=df_b['time'], y=df_b.get('safetyStockLine'), mode='lines', line_shape='hv', name='打分下界(安全线)', line=dict(color='#ef4444', width=2, dash='dot')))
    
    c_data_t = np.column_stack((df_b.get('sigma_d'), df_b.get('sqrt_lt'), df_b.get('sigma_dl'), df_b.get('target_weeks')))
    fig2.add_trace(go.Scatter(x=df_b['time'], y=df_b.get('targetLevel'), mode='lines', line_shape='hv', name='打分上界(水位)', line=dict(color='#eab308', width=2), fill='tonexty', fillcolor='rgba(134, 239, 172, 0.2)', customdata=c_data_t, hovertemplate="目标水位: %{y:.0f}件 (%{customdata[3]:.1f}周)<br>---<br>σD: %{customdata[0]:.1f} | √LT: %{customdata[1]:.1f} | σDL: %{customdata[2]:.1f}件"))
    fig2.add_trace(go.Scatter(x=df_b['time'], y=df_b.get('totalPipeline'), mode='lines', line_shape='hv', name='总管线(库+途)', line=dict(color='#93c5fd', width=2, dash='dash'), customdata=df_b.get('pipeline_weeks'), hovertemplate="总管线: %{y:.0f}件 (%{customdata:.1f}周)"))
    fig2.add_trace(go.Scatter(x=df_b['time'], y=df_b.get('inventory'), mode='lines+markers', name='期末在库', line=dict(color='#0ea5e9', width=4), marker=dict(size=6, color='white', line=dict(width=2, color='#0ea5e9')), customdata=df_b.get('inventory_weeks'), hovertemplate="在库: %{y:.0f}件 (%{customdata:.1f}周)"))

    fig2.add_vline(x=11.5, line_dash="dash", line_color="#94a3b8", annotation_text="T0", annotation_position="top left")
    fig2.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), plot_bgcolor="white", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified")
    fig2.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9'); fig2.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#f1f5f9')
    
    return fig1, fig2

# ==========================================
# UI 布局：顶层全局区域
# ==========================================
header_col1, header_col2 = st.columns([2, 1.5])
with header_col1:
    st.title("🚢 T0 SKU 供应链协同寻优沙盘")
    st.caption("v11.0 深度防线版 | 拓宽 Z 值上限至 3.5，全精度步长匹配极端长尾波动")

with header_col2:
    t0_date = st.date_input("🗓️ 设定 T0 历史切片周一日期", value=pd.to_datetime('2026-04-13').date())
    uploaded_file = st.file_uploader("导入 SKU 数据 (.xlsx 或 .json)", type=['xlsx', 'json'])
    
    if uploaded_file is not None:
        file_sig = f"{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.last_uploaded_file != file_sig:
            try:
                if uploaded_file.name.endswith('.json'):
                    data = json.load(uploaded_file)
                    if isinstance(data, list) and len(data) > 0 and 'id' in data[0]:
                        st.session_state.sku_data_list = data
                        st.session_state.force_recompute_scores = True
                        st.session_state.last_uploaded_file = file_sig
                        st.success("JSON 数据导入成功！")
                elif uploaded_file.name.endswith('.xlsx'):
                    df = pd.read_excel(uploaded_file)
                    data = parse_excel_to_skus(df, t0_date)
                    if data:
                        st.session_state.sku_data_list = data
                        st.session_state.force_recompute_scores = True
                        st.session_state.last_uploaded_file = file_sig
                        st.success(f"Excel 解析成功！成功导入 {len(data)} 个 SKU。")
                    else:
                        st.error("Excel 解析失败：请确保列名与标准模板一致。")
            except Exception as e:
                st.error(f"文件读取异常: {str(e)}")
            
    st.download_button("📊 下载标准 Excel 数据模板", data=generate_excel_template(), file_name="sku_data_template.xlsx")

st.divider()

if not st.session_state.sku_data_list:
    st.info("👋 欢迎使用 T0 SKU 供应链寻优沙盘！当前暂无数据。请在右上角上传您的数据文件。")
    st.stop()

# ==========================================
# 全局预计算与 O(1) 提取
# ==========================================
g_lt = st.session_state.get('global_lt', 10)
g_ss = st.session_state.get('global_ss', 2)
g_rp = st.session_state.get('global_review_period', 1)
g_offset = st.session_state.get('global_offset', 0)
g_moq = st.session_state.get('global_moq', 0)
g_pen_out = st.session_state.get('global_pen_out', 5.0)
g_pen_ss = st.session_state.get('global_pen_ss', 1.0)
g_pen_over = st.session_state.get('global_pen_over', 0.1)
g_discount = st.session_state.get('global_discount', 0.95)

global_params = (g_lt, g_ss, g_moq, g_pen_out, g_pen_ss, g_pen_over, g_rp, g_offset, g_discount)

if st.session_state.get('last_global_params') != global_params or st.session_state.get('force_recompute_scores'):
    with st.spinner(f"正在全局探测 {len(st.session_state.sku_data_list)} 个 SKU 的优化空间，提取最高优预警项..."):
        new_cache = {}
        for s in st.session_state.sku_data_list:
            bz, ba = auto_optimize(s, g_lt, g_ss, g_moq, g_pen_out, g_pen_ss, g_pen_over, g_rp, g_offset, g_discount, overrides=None)
            _, ai_score, _, _, _ = run_simulation(s, g_lt, g_ss, g_moq, bz, ba, g_pen_out, g_pen_ss, g_pen_over, g_rp, g_offset, g_discount, overrides=None)
            _, leg_score, _ = run_legacy_simulation(s, g_lt, g_ss, g_moq, g_pen_out, g_pen_ss, g_pen_over, g_discount, z_base_ref=bz)
            new_cache[s['id']] = {
                'ai_score': round(ai_score),
                'legacy_score': round(leg_score),
                'diff': round(leg_score - ai_score),
                'best_z': bz,     
                'best_a': ba
            }
        st.session_state.sku_scores_cache = new_cache
        st.session_state.last_global_params = global_params
        st.session_state.force_recompute_scores = False

sorted_skus = sorted(
    st.session_state.sku_data_list,
    key=lambda x: st.session_state.sku_scores_cache.get(x['id'], {}).get('diff', 0),
    reverse=True
)

def format_sku_option(sku_id):
    scores = st.session_state.sku_scores_cache.get(sku_id, {})
    diff = scores.get('diff', 0)
    ai_sc = scores.get('ai_score', 0)
    leg_sc = scores.get('legacy_score', 0)
    return f"{sku_id} (优化空间: {diff} | 传统: {leg_sc} | AI: {ai_sc})"

selected_sku_id = st.selectbox("📌 选择要分析的 SKU (已按“AI新策略优化空间”智能降序排列)", [s['id'] for s in sorted_skus], format_func=format_sku_option)
current_sku = next(s for s in st.session_state.sku_data_list if s['id'] == selected_sku_id)

st.markdown(f"""
    <div class="sku-info-bar">
        <div class="sku-info-item">🌍 市场: <span>{current_sku.get('market', '-')}</span></div>
        <div class="sku-info-item">🏪 渠道: <span>{current_sku.get('channel', '-')}</span></div>
        <div class="sku-info-item">📦 品类: <span>{current_sku.get('category', '-')}</span></div>
        <div class="sku-info-item">⭐ 层级: <span>{current_sku.get('level', '-')}</span></div>
    </div>
""", unsafe_allow_html=True)


# ==========================================
# 控制台横向铺开区域
# ==========================================
st.markdown("### ⚙️ 全局物理硬约束")

col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
lt = col_p1.slider("海运 LT (周)", min_value=0, max_value=24, value=st.session_state.global_lt, key="global_lt", step=1)
ss = col_p2.slider("安全库存底座 (周)", min_value=0, max_value=12, value=st.session_state.global_ss, key="global_ss", step=1)

review_period = col_p3.slider("发货频率 (发货周期/周)", min_value=1, max_value=8, value=st.session_state.global_review_period, key="global_review_period", step=1)
if st.session_state.global_review_period == 1:
    col_p4.slider("T0 距下次发货节点 (周)", min_value=0, max_value=1, value=0, disabled=True)
    st.session_state.global_offset = 0
    offset = 0
else:
    offset = col_p4.slider("T0 距下次发货节点 (周)", min_value=0, max_value=st.session_state.global_review_period - 1, value=st.session_state.global_offset, key="global_offset", step=1)
    
moq = col_p5.slider("起订量 MOQ", min_value=0, max_value=500, value=st.session_state.global_moq, key="global_moq", step=10)

st.markdown("### ⚖️ 代价函数设定与 AI 寻优")
col_c1, col_c2, col_c3, col_c4, col_c5 = st.columns(5)
pen_out = col_c1.number_input("🚨 断货罚分权重 (/件)", value=st.session_state.global_pen_out, key="global_pen_out", step=0.5)
pen_ss = col_c2.number_input("⚠️ 安全线罚分权重 (/件)", value=st.session_state.global_pen_ss, key="global_pen_ss", step=0.1)
pen_over = col_c3.number_input("💰 压货罚分权重 (/件)", value=st.session_state.global_pen_over, key="global_pen_over", step=0.05)
discount_factor = col_c4.number_input("📉 时间衰减因子", value=st.session_state.global_discount, key="global_discount", step=0.01)

current_sku_constraints = (selected_sku_id, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor)
if st.session_state.get('last_sku_constraints') != current_sku_constraints:
    st.session_state.manual_overrides = {}
    
    cached_data = st.session_state.sku_scores_cache.get(selected_sku_id, {})
    bz = cached_data.get('best_z', 1.0)
    ba = cached_data.get('best_a', 0.5)
    
    st.session_state.z_base, st.session_state.a_base = bz, ba
    st.session_state.z_slider, st.session_state.a_slider = bz, ba
    st.session_state.z_hyb, st.session_state.a_hyb = bz, ba
    st.session_state.last_sku_constraints = current_sku_constraints

with col_c5:
    st.markdown("<br>", unsafe_allow_html=True)
    st.button("✨ 恢复当前 SKU 的 AI 理论最优解", on_click=restore_ai_optimal, args=(selected_sku_id,), use_container_width=True, type="primary")

st.markdown("#### 🧠 AI 推荐决策参数设定")
col_a1, col_a2, col_a3 = st.columns([1, 1, 3])
# 【修改点】：Z值的滑块上限同步调高到 3.5
z_val = col_a1.slider("安全系数 (Z值)", min_value=0.0, max_value=3.5, step=0.1, key="z_slider")
alpha = col_a2.slider("平滑系数 (α)", min_value=0.1, max_value=1.0, step=0.1, key="a_slider")


base_sim_data, base_score, original_weeks, base_qty, static_std_dev = run_simulation(
    current_sku, lt, ss, moq, z_val, alpha, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=None)

hyb_sim_data, hyb_score, _, hyb_qty, _ = run_simulation(
    current_sku, lt, ss, moq, st.session_state.z_hyb, st.session_state.a_hyb, pen_out, pen_ss, pen_over, review_period, offset, discount_factor, overrides=st.session_state.manual_overrides)


# ==========================================
# 图表区 1：🤖 纯粹 AI 理论最优解
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.subheader("🤖 纯粹 AI 理论最优解 (Baseline)")

col_ai1, col_ai2, col_ai3, col_ai4 = st.columns(4)
col_ai1.markdown(f"<div class='kpi-card'><div class='kpi-title'>总罚分 (有效区间内)</div><div class='kpi-value'>{round(base_score):,}</div></div>", unsafe_allow_html=True)
col_ai2.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景断货周数</div><div class='kpi-value' style='color:#ef4444;'>{len([d for d in base_sim_data if d['stockout'] > 0])}</div></div>", unsafe_allow_html=True)
col_ai3.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景规划总发货</div><div class='kpi-value' style='color:#8b5cf6;'>{base_qty:,} 件</div></div>", unsafe_allow_html=True)
col_ai4.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景期末在库</div><div class='kpi-value' style='color:#3b82f6;'>{base_sim_data[-1]['inventory'] if base_sim_data else 0:,} 件</div></div>", unsafe_allow_html=True)

fig1_b, fig2_b = build_charts(base_sim_data, original_weeks, lt, mode='ai')
chart_col1, chart_col2 = st.columns(2)
with chart_col1:
    st.markdown("##### 📊 供需与发货动作流")
    st.plotly_chart(fig1_b, use_container_width=True, key="ai_chart1")
with chart_col2:
    st.markdown("##### 🛡️ 自适应目标区间与管线健康度")
    st.plotly_chart(fig2_b, use_container_width=True, key="ai_chart2")


# ==========================================
# 图表区 2：🧑‍🔧 人工干预与 AI 协同重算
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.subheader("🧑‍🔧 人工干预与 AI 协同重算 (Human-in-the-loop)")

valid_order_len = max(0, original_weeks - lt)
data_dict = {"指标": ["AI 理论建议 (件)", "👉 人工干预 (件)"]}
for idx in range(valid_order_len):
    w_label = f"W{idx+16}"
    ai_val = base_sim_data[idx].get('simOrder', 0)
    man_val = st.session_state.manual_overrides.get(idx, None)
    data_dict[w_label] = [ai_val, man_val]

df_editor = pd.DataFrame(data_dict)

col_config = {"指标": st.column_config.TextColumn("指标", disabled=True)}
for idx in range(valid_order_len):
    w_label = f"W{idx+16}"
    col_config[w_label] = st.column_config.NumberColumn(w_label, min_value=0, step=1)

st.markdown("<p style='font-size:14px; color:#475569; margin-bottom:5px;'>👇 <b>横向数据干预网格</b>：第二行为人工干预专属通道，输入后直接生效。</p>", unsafe_allow_html=True)

edited_df = st.data_editor(
    df_editor,
    column_config=col_config,
    hide_index=True,
    use_container_width=True,
    height=120,
    key="override_editor"
)

new_overrides = {}
manual_row = edited_df.iloc[1] 
for idx in range(valid_order_len):
    w_label = f"W{idx+16}"
    val = manual_row[w_label]
    if pd.notna(val) and str(val).strip() != "":
        try:
            new_overrides[idx] = float(val)
        except ValueError:
            pass
            
if new_overrides != st.session_state.manual_overrides:
    st.session_state.manual_overrides = new_overrides
    st.rerun()

score_color = "#10b981" if hyb_score < base_score else ("#ef4444" if hyb_score > base_score else "#0f172a")

col_h1, col_h2, col_h3, col_h4 = st.columns(4)
col_h1.markdown(f"<div class='kpi-card'><div class='kpi-title'>协同总罚分 (Z={st.session_state.z_hyb:.1f}, α={st.session_state.a_hyb:.1f})</div><div class='kpi-value' style='color:{score_color};'>{round(hyb_score):,}</div></div>", unsafe_allow_html=True)
col_h2.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景断货周数</div><div class='kpi-value' style='color:#ef4444;'>{len([d for d in hyb_sim_data if d['stockout'] > 0])}</div></div>", unsafe_allow_html=True)
col_h3.markdown(f"<div class='kpi-card'><div class='kpi-title'>干预后全景发货量</div><div class='kpi-value' style='color:#f97316;'>{hyb_qty:,} 件</div></div>", unsafe_allow_html=True)
with col_h4:
    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    st.button("✨ 锁定干预值，让AI重新寻优兜底", on_click=reoptimize_hybrid, args=(current_sku, lt, ss, moq, pen_out, pen_ss, pen_over, review_period, offset, discount_factor), use_container_width=True, type="secondary", key="hyb_btn")

fig1_h, fig2_h = build_charts(hyb_sim_data, original_weeks, lt, mode='hybrid')
chart_col3, chart_col4 = st.columns(2)
with chart_col3:
    st.plotly_chart(fig1_h, use_container_width=True, key="hyb_chart1")
with chart_col4:
    st.plotly_chart(fig2_h, use_container_width=True, key="hyb_chart2")


# ==========================================
# 图表区 3：传统实际发货逻辑对照 
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.subheader("🏛️ 传统发货策略回测对照 (10+2 前瞻回溯)")

leg_sim_data, leg_score, leg_qty = run_legacy_simulation(
    current_sku, lt, ss, moq, pen_out, pen_ss, pen_over, discount_factor, z_base_ref=z_val)

col_l1, col_l2, col_l3, col_l4 = st.columns(4)
col_l1.markdown(f"<div class='kpi-card'><div class='kpi-title'>传统策略有效总罚分</div><div class='kpi-value' style='color:#ef4444;'>{round(leg_score):,}</div></div>", unsafe_allow_html=True)
col_l2.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景断货周数</div><div class='kpi-value' style='color:#ef4444;'>{len([d for d in leg_sim_data if d['stockout'] > 0])} 周</div></div>", unsafe_allow_html=True)
col_l3.markdown(f"<div class='kpi-card'><div class='kpi-title'>传统有效发货量</div><div class='kpi-value' style='color:#ec4899;'>{leg_qty:,} 件</div></div>", unsafe_allow_html=True)
col_l4.markdown(f"<div class='kpi-card'><div class='kpi-title'>全景期末在库</div><div class='kpi-value' style='color:#3b82f6;'>{leg_sim_data[-1]['inventory'] if leg_sim_data else 0:,} 件</div></div>", unsafe_allow_html=True)

fig1_leg, fig2_leg = build_charts(leg_sim_data, original_weeks, lt, mode='legacy')

leg_chart_col1, leg_chart_col2 = st.columns(2)
with leg_chart_col1:
    st.plotly_chart(fig1_leg, use_container_width=True, key="leg_chart1")
with leg_chart_col2:
    st.plotly_chart(fig2_leg, use_container_width=True, key="leg_chart2")
