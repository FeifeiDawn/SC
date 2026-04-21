leg_chart_col1, leg_chart_col2 = st.columns(2)
with leg_chart_col1:
    st.plotly_chart(fig1_leg, use_container_width=True, key="leg_chart_1")
with leg_chart_col2:
    st.plotly_chart(fig2_leg, use_container_width=True, key="leg_chart_2")
