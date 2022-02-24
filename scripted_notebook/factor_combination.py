# -*- coding: utf-8 -*-
# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.13.6
#   kernelspec:
#     display_name: 'Python 3.7.10 64-bit (''multifactor'': conda)'
#     name: python3
# ---

# %% [markdown]
# #### Set up
# Run this section before anything else

# %%
import os 
os.chdir('../')

from datetime import datetime, timedelta
import src.dataloader as dl
import pandas as pd
import rqdatac as rq
from src.constants import *
import scipy
import statsmodels as sm
import numpy as np
import seaborn as sns
import pathos
from tqdm.notebook import tqdm
import multiprocessing
import pickle
import matplotlib.pyplot as plt


# %%
def applyParallel(dfGrouped, func):
    #parrallel computing version of pd.groupby.apply, works most of the time but not always
    #I mainly use it for cases where func takes in a dataframe and outputs a dataframe or a series
    with pathos.multiprocessing.ProcessPool(pathos.helpers.cpu_count()) as pool:
        ret_list = pool.map(func, [group for name, group in dfGrouped])
    return pd.concat(ret_list)


# %%
dl.rq_initialize()


# %%
def sort_index_and_col(df) -> pd.DataFrame:
    #sort the dataframe by index and column
    return df.sort_index(axis=0).reindex(sorted(df.columns), axis=1)
    #the following might achieve the same result in a cleaner way
    # return df.sort_index(axis=0).sort_index(axis=1)


# %%
results = dl.load_basic_info()

# %% [markdown]
# #### Load Industry Data

# %%
df_indus_mapping = dl.load_industry_mapping()

# %% [markdown]
# #### Load Index Data
# In hiearachical backtesting we need weights of index(e.g. CSI300) data to make the portfolio to stay industry-neutral with the index.
#
# Currently index data is assumed to be uniformly weighted among all stocks

# %%
# CSI 300 沪深300
df_index = pd.read_csv(dl.DATAPATH + 'index_data/' + 'sh000300.csv',usecols=['date', 'open','close','change'],index_col=['date']).sort_index(ascending=True)
df_index.columns = ['CSI_300_' + col for col in df_index.columns]
df_index.index = df_index.index.values.astype('datetime64')
df_index

# %%
((df_index['CSI_300_change'] + 1).cumprod() - 1).plot()

# %% [markdown]
# #### Download factor data

# %%
with open('./Data/raw_data/stock_names.h5', 'rb') as fp:
     stock_names = pickle.load(fp)

# %%
# value factor
# value = ['pe_ratio_ttm','pcf_ratio_ttm', 'pcf_ratio_total_ttm','pb_ratio_ttm','book_to_market_ratio_ttm','dividend_yield_ttm', 'ps_ratio_ttm']
# dl.download_factor_data(stock_names, value, START_DATE,END_DATE)

# %% [markdown]
# ### Data Preprocessing Part 1

# %% [markdown]
# Major Steps: 
#
# 0) Read all csv's and concatenate the desired column from each dataframe
#
# 1) Filter out data before START_DATE and after END_DATE(backtesting period) from the raw stock data. 
#
# - 剔除不在回测区间内的股票信息
#
# 2) Filter listed stocks
#
# - 选出回测区间内每只股票上市的时间。这一步是为了步骤3，因为在每个选股日筛选ST或者停牌股的前提是股票在该选股日已上市。
#
# 3) Filter out ST stocks, suspended stocks and stocks that are listed only within one year
# - 剔除ST，停牌和次新股（上市未满一年的股票）
#

# %%
# step 0
df_backtest = pd.concat(results, axis=0).rename(columns={'code': 'stock'}).loc[:, INDEX_COLS + BASIC_INFO_COLS]
df_backtest['date'] = pd.to_datetime(df_backtest['date'])

# step 1
df_backtest = df_backtest[ (START_DATE <= df_backtest['date']) & (df_backtest['date'] <= END_DATE) ]
df_backtest['stock'] = df_backtest['stock'].apply(lambda stock: dl.normalize_code(stock))

# have a (date_stock) multi-index dataframe 
df_backtest = df_backtest.set_index(INDEX_COLS).sort_index()
df_backtest = df_backtest.unstack(level=1).stack(dropna=False)


# %%
def get_factor_df(factor):
    print(factor)
    factor_path = os.path.join(DATAPATH, 'factor', factor + ".h5")
    return pd.read_hdf(factor_path)

for factor in FACTORS['value']:
    df_backtest[factor] = get_factor_df(factor).sort_index().values
# with pathos.multiprocessing.ProcessPool(pathos.helpers.cpu_count()) as pool:
#     factor_results = pool.map(get_factor_df, FACTORS['value'])
#     df_factor = pd.concat(factor_results, axis=1)

# %%
# check stock index
stock_names = df_backtest.index.get_level_values(1).unique()
stock_names

# %%
# step 2
# get the listed date
listed_dates = {dl.normalize_code(result['code'][0]): result['date'].min() for result in results}
listed_dates = pd.DataFrame(pd.Series(listed_dates), columns=['listed_date']).sort_index().astype('datetime64')
# left join with dataframe 'listed_dates'
df_backtest = df_backtest.merge(listed_dates, left_on = 'stock', right_index=True, how='left')
# create a new variable called 'is_listed' to check if a certain stock is listed at that given date
df_backtest['is_listed_for_one_year'] = (df_backtest.index.get_level_values(level=0).values - df_backtest['listed_date'].values >= pd.Timedelta('1y'))

# %%
# number of non-listed stocks along the time
non_listed = df_backtest[~df_backtest['is_listed_for_one_year']]
num_nonlisted_stock = non_listed.groupby(level=0).count()['is_listed_for_one_year']
num_nonlisted_stock.plot.line()

# %%
df_backtest

# %%
# load st/suspend data from Ricequant
df_is_st = dl.load_st_data(stock_names)
df_is_suspended = dl.load_suspended_data(stock_names)

# %%
# step 3
#create ST and suspended columns
df_backtest['is_st'] = df_is_st.values
df_backtest['is_suspended'] = df_is_suspended.values
# filter out stocks that are listed within a year
#filter out ST and suspended stocks, filter data by the stock's listed date
df_backtest = df_backtest.loc[ (~df_backtest['is_st']) & (~df_backtest['is_suspended']) & (df_backtest['is_listed_for_one_year']), BASIC_INFO_COLS + TEST_FACTORS]
#keep data only on the rebalancing dates
rebalancing_dates = pd.date_range(start=START_DATE, end=END_DATE, freq='BM')
df_backtest = df_backtest[df_backtest.index.get_level_values(0).isin(rebalancing_dates)]

# %%
# the current rebalancing date is the last trading day of the current period
# 'next_period_open' is defined as the stock's open price on the next relancing date
# 'next_period_return' is the generated return by holding a stock from EOD of current rebalancing date to the start of the next rebalancing date
df_backtest['next_period_open'] = df_backtest['open'].groupby(level=1).shift(-1).values
df_backtest['next_period_return'] = (df_backtest['next_period_open'].values - df_backtest['close'].values) / df_backtest['close'].values
df_backtest = df_backtest[df_backtest.index.get_level_values(0) != df_backtest.index.get_level_values(0).max()]

# %%
df_preprocess1 = df_backtest.copy()

# %%
df_backtest


# %% [markdown]
# ## Data Preprocessing part 2
# ### 1) Replace Outliers with the corresponding threshold
# ### 2) Standardization - Subtract mean and divide by std
# ### 3) Fill missing values with 0
#

# %%
def remove_outlier(df, n=3,):
    #for any factor, if the stock's factor exposure lies more than n times MAD away from the factor's median, 
    # reset that stock's factor exposure to median + n * MAD/median - n* MAD
    med = df.median(axis=0)
    MAD = (df - med).abs().median()
    upper_limit = med + n * MAD
    lower_limit = med - n * MAD
    # print(f"lower_limit = {lower_limit}, upper_limit = {upper_limit}")
    #pd.DataFrame.where replaces data in the dataframe by 'other' where the condition is False
    df = df.where(~( ( df > upper_limit) & df.notnull() ), other = upper_limit, axis=1)
    df = df.where(~( ( df < lower_limit) & df.notnull() ), other = lower_limit, axis=1)
    return df


# %%
# step 1
df_preprocess1[TEST_FACTORS] = applyParallel(df_preprocess1[TEST_FACTORS].groupby(level=0), remove_outlier).values

# %%
df_preprocess1


# %%
def standardize(df):
    #on each rebalancing date, each standardized factor has mean 0 and std 1
    return (df - df.mean()) / df.std()

df_preprocess1[TEST_FACTORS] = applyParallel(df_preprocess1[TEST_FACTORS].groupby(level=0), standardize).values

# %%
df_preprocess1.groupby(level=0)[TEST_FACTORS].agg(['mean', 'std'])

# %%
df_preprocess1[TEST_FACTORS] = df_preprocess1[TEST_FACTORS].fillna(0).values

# %%
df_preprocess1

# %%
#data missing issue, simply filter them out, otherwise would negatively impact later results
df_preprocess1 = df_preprocess1[df_preprocess1['next_period_return'].notnull() & df_preprocess1['market_value'].notnull()]

# %%
df_backtest = df_preprocess1.copy()

# %%
df_preprocess1

# %% [markdown]
# # Single-Factor Backtesting

# %% [markdown]
# 同一类风格因子选多个(5-10)集中测试，下面是常见的几种测试方法，对于每一个方法相应的不同评价指标，我们最后用折线图/柱状图和一张完整的表格/dataframe来展示，详见华泰研报单因子测试中的任意一篇
#
# - 回归法
# - IC值
# - 分层回测（既要与基准组合比较，也要比较超额收益的时间序列）

# %%
SINGLE_FACTOR = 'PE_TTM'

# %%
df_backtest = df_backtest.merge(df_indus_mapping, how='left', left_on='stock', right_index=True)
print(df_backtest.shape)
df_backtest = df_backtest[df_backtest['pri_indus_code'].notnull()]
print(df_backtest.shape)

# %%
df_backtest


# %% [markdown]
# # T-Value Analysis
# 回归法

# %%
def wls_tval_coef(df):
    #obtain the t-value in WLS of the tested factor
    # 函数内需要用包要额外在这里加上
    import statsmodels.formula.api as smf
    import pandas as pd
    SINGLE_FACTOR = 'PE_TTM'

    # Weighted Least Square(WLS) uses the square root of market cap of each stock
    # 使用加权最小二乘回归，并以个股流通市值的平方根作为权重
    # other than the factor of interest, we also regress on the industry for neutralization
    # 同时对要测试的因子和行业因子做回归（个股属于该行业为1，否则为0），消除因子收益的行业间差异
    wls_result = smf.wls(formula = f"next_period_return ~ pri_indus_code + {SINGLE_FACTOR}", 
                    data=df, weights = df['market_value'] ** 0.5).fit()
    result_tval_coef = pd.Series( {'t_value': wls_result.tvalues.values[0], 'coef': wls_result.params.values[0], 
                         } )
    # result_resid = pd.Series( {'resid': wls_result.resid.values} )
    return result_tval_coef.to_frame().transpose()


# %%
#get the t-value for all periods
wls_results_tval_coef = applyParallel(df_backtest.groupby(level=0), wls_tval_coef)
wls_results_tval_coef.index = df_backtest.index.get_level_values(level=0).unique()

# %%
# get a summary result from the t-value series
# 回归法的因子评价指标

# t值序列绝对值平均值
tval_series_mean = wls_results_tval_coef['t_value'].abs().mean()
# t 值序列绝对值大于 2 的占比
large_tval_prop = (wls_results_tval_coef['t_value'].abs() > 2).sum() / wls_results_tval_coef.shape[0]
# t 值序列均值的绝对值除以 t 值序列的标准差
standardized_tval = wls_results_tval_coef['t_value'].mean() / wls_results_tval_coef['t_value'].std()
# 因子收益率序列平均值
coef_series_mean = wls_results_tval_coef['coef'].mean()
# 因子收益率均值零假设检验的 t 值
coef_series_t_val = scipy.stats.ttest_1samp(wls_results_tval_coef['coef'], 0).statistic

# %%
print('t值序列绝对值平均值：', '{:0.4f}'.format(tval_series_mean))
print('t值序列绝对值大于2的占比：', '{percent:.2%}'.format(percent = large_tval_prop))
print('t 值序列均值的绝对值除以 t 值序列的标准差：', '{:0.4f}'.format(standardized_tval))
print('因子收益率均值：', '{percent:.4%}'.format(percent=coef_series_mean))
print('因子收益率均值零假设检验的 t 值：', '{:0.4f}'.format(coef_series_t_val))


# %% [markdown]
# ## Information Coefficient Analysis

# %%
#Rank IC is defined by the spearman correlation of the factor residual(after market-value and industry neutralizations)
#with next period's return

# %%
# data preprocess of IC analysis
# 因子值IC值计算之前的预处理
# 因子值在去极值、标准化、去空值处理后，在截面期上用其做因变量对市值因子及行业
# 因子（哑变量）做线性回归，取残差作为因子值的一个替代

def wls_factor_resid(df):
    import statsmodels.formula.api as smf
    wls_result = smf.wls(formula = f"PE_TTM ~ market_value + pri_indus_code", 
                    data=df).fit()
    return wls_result.resid


# %%
factor_resids = applyParallel(df_backtest.groupby(level=0), wls_factor_resid)
factor_resids = factor_resids.rename('PE_TTM_resid')
factor_resids

# %%
df_backtest = df_backtest.merge(factor_resids, how='left', left_index=True, right_index=True)

# %%
df_backtest


# %%
# 下一期所有个股的收益率向量和当期因子的暴露度向量的相关系数
# use Spearman's rank correlation coefficient by default. Another choice is Pearson
def cross_sectional_ic(df):
    return df[['next_period_return', 'PE_TTM_resid']].corr(method='spearman').iloc[0, 1]
ic_series = df_backtest.groupby(level=0).apply(cross_sectional_ic)

# %%
ic_series

# %%
ic_series_mean = ic_series.mean()
ic_series_std = ic_series.std()
ir = ic_series_mean / ic_series_std
ic_series_cum = ic_series.cumsum()
ic_pos_prop = (ic_series > 0).sum() / ic_series.shape[0]

# %%
print('IC 均值:','{:0.4f}'.format(ic_series_mean))
print('IC 标准差:','{:0.4f}'.format(ic_series_std))
print('IR 比率:','{percent:.2%}'.format(percent=ir))
print('IC 值序列大于零的占比:','{percent:.2%}'.format(percent=ic_pos_prop))

# %%
# IC 值累积曲线——随时间变化效果是否稳定
ic_series_cum.plot()

# %%
df_backtest = df_backtest.drop(columns = ['PE_TTM_resid'])

# %% [markdown]
# ## Hiearachical Backtesting
# 分层回测

# %%
NUM_GROUPS = 5
GROUP_NAMES = [f"group{i}_weight" for i in range(1, NUM_GROUPS + 1)]

# %%
from functools import partial
assigned_group = df_backtest.groupby('pri_indus_code')['PE_TTM'].apply(partial(pd.qcut, q=5, labels=range(5)))

# %%
fig = plt.figure(figsize = (10, 5))
num_stocks_per_indus = df_backtest[df_backtest.index.get_level_values(0) == '2011-01-31'].groupby('pri_indus_code')['PE_TTM'].count()

plt.bar(*zip(*num_stocks_per_indus.items()))
 
plt.xlabel("Industry")
plt.ylabel("Number of Companies")
plt.show()

# %%
#Here for simplicity we assume that index weight is a uniform portfolio over all stocks
#TODO: use the real index constituent weights instead
df_backtest['index_weight'] = df_backtest.groupby(level=0).apply(lambda df: pd.Series([1/df.shape[0]] * df.shape[0])).values


# %%
#merge the industry weights of the index onto the backtesting dataframe
def set_index_indus_weight(df):
    index_indus_weight = df.groupby('pri_indus_code')['index_weight'].sum().rename('index_indus_weight')
    df = df.merge(index_indus_weight, how='left', left_on='pri_indus_code', right_index=True)
    return df
#'index_indus_weight' = this stock's industry weight in the benchmark index
if 'index_indus_weight' not in df_backtest.columns:
    df_backtest = applyParallel(df_backtest.groupby(level=0), set_index_indus_weight)

# %%
df_backtest

# %%
import numpy as np
# hiearchical backtesting is pretty hard to implement using purely vectorization/parrallelization, and I have to use for loop at least once.
def get_group_weight_by_industry(num_stocks, num_groups) -> np.array:
    """
    precondition: the stocks need to be sorted by factor exposure
    @num_stocks: the number of stocks in this industry
    @num_groups: the number of portfolio groups to be constructed
    
    returns: an intermediary (num_stocks x num_groups) weight matrix specifying the weight of 
             each stock in each group. Here weights within each group(column sum) adds up to 1.
             This is not the final weight matrix because there are many industries(so that weights within 
             each group should actually be smaller than one) but the returned
             weight matrix represents only one industry. 
    
    if you want to understand the algorithm deeper, print some intermediary outputs
    """
    num_rows = min(num_groups, num_stocks)
    num_cols = max(num_groups, num_stocks)
    weight_mat = np.zeros((num_rows, num_cols))
    remaining = 0
    j = 0
    row_budget = num_cols
    col_budget = num_rows
    for i in range(num_rows):
        # print(f"i = {i}")
        start = col_budget - remaining
        # print(f"start = {start}")
        weight_mat[i, j] = start
        offset = (row_budget - start) // col_budget
        # print(f"offset = {offset}")
        weight_mat[i, j + 1: j + 1 + offset] = col_budget
        remaining = row_budget - offset * col_budget - start
        j = j + 1 + offset
        if j < num_cols:
            weight_mat[i, j] = remaining
        
    weight_mat = weight_mat if num_groups > num_stocks else weight_mat.transpose()
    weight_mat_normalized = weight_mat / weight_mat.sum(axis=0)
    return weight_mat_normalized

def get_weight_df_by_industry(df: pd.DataFrame) -> pd.DataFrame:
    """get the weight dataframe for each industry"""
    #sort by the factor exposure
    df = df.sort_values(by='PE_TTM') 
    stock_names = df.index.get_level_values(1)
    #get weight matrix first
    weight_mat = get_group_weight_by_industry(stock_names.shape[0], NUM_GROUPS)
    df[GROUP_NAMES] = weight_mat
    return df


# %%
def get_group_weight_by_date(df_backtest_sub):
    #get the intermediary weights in each group on each rebalancing date
    df_backtest_sub = df_backtest_sub.groupby('pri_indus_code').apply(get_weight_df_by_industry).droplevel(0).sort_index(level=1)
    """
    we need to make the group portfolio industry-neutral with the index. That is, industry weights should be the same in both
    the group portfolio and the index. 
    """
    #multiply each stock's intermediary weight by its industry weight. since the intermediary weight within each group within each industry adds up to 1(as explained in the previous function),
    #after this operation the final stock weight within each group should add up to 1.
    df_backtest_sub[GROUP_NAMES] = np.multiply(df_backtest_sub[GROUP_NAMES].values, df_backtest_sub['index_indus_weight'].values[:, np.newaxis])
    return df_backtest_sub


# %%
df_backtest = df_backtest.groupby(level=0).apply(get_group_weight_by_date)

# %%
df_backtest

# %%
df_backtest.groupby(level=0)[GROUP_NAMES].sum() #looks good


# %%
def get_group_returns_by_date(df_backtest_sub):
    group_returns = df_backtest_sub[GROUP_NAMES].values.transpose() @ df_backtest_sub['next_period_return'].values
    group_returns = pd.Series(group_returns, index=GROUP_NAMES)
    return group_returns

group_returns_by_date = df_backtest.groupby(level=0).apply(get_group_returns_by_date)
group_returns_by_date

# %%
group_cum_returns= (group_returns_by_date + 1).cumprod(axis=0)
group_cum_returns

# %%
group_cum_returns.plot()

# %% [markdown]
# ## Factor Combination

# %%
factor_type = 'value'
COMBINE_FACTORS = \
['ev_ttm',
 'pe_ratio_ttm',
 'pb_ratio_ttm',
 'peg_ratio_ttm',
 'book_to_market_ratio_ttm',
 'pcf_ratio_ttm',
 'ps_ratio_ttm']

file_names = [os.path.join(DATAPATH, 'factor', factor_type, factor + ".h5") for factor in COMBINE_FACTORS]
df_factors = [pd.read_hdf(file_name).sort_index() for file_name in file_names]
df_factor = pd.concat(df_factors, axis=1)
df_factor = df_factor[df_factor.index.get_level_values(1).isin(rebalancing_dates)]
df_factor = df_factor.reset_index().rename(columns={'order_book_id': 'stock'} ).set_index(['date', 'stock']).sort_index()  
n = len(COMBINE_FACTORS)

# %%
df_backtest.columns

# %%
BASIC_INFO_COLS = ['market_value', 'open', 'close', 'next_period_return',
                   'secon_indus_code']

# %%
TEST_FACTORS

# %%
df_backtest_comb_factors = df_backtest[BASIC_INFO_COLS]

# %%
df_backtest.drop(columns=TEST_FACTORS).merge(df_factor, how='left', left_index=True, right_index=True)

# %%
df_factor

# %%
df_factor = pd.concat(df_factors, axis=1)

# %%
df_factor


# %%
def get_ic_series(factor, df_backtest=df_backtest):
    def wls_factor_resid(df):
        import statsmodels.formula.api as smf
        wls_result = smf.wls(formula = f"{factor} ~ 0 + market_value + C(pri_indus_code)", 
                        data=df, weights = df['market_value'] ** 0.5).fit()
        return wls_result.resid
    if f'{factor}_resid' not in df_backtest.columns:
        factor_resids = applyParallel(df_backtest.groupby(level=0), wls_factor_resid)
        factor_resids = factor_resids.rename(f'{factor}_resid')
        df_backtest = df_backtest.merge(factor_resids, how='left', left_index=True, right_index=True)
    def cross_sectional_ic(df):
        return df[['next_period_return', f'{factor}_resid']].corr(method='spearman').iloc[0, 1]
    ic_series = df_backtest.groupby(level=0).apply(cross_sectional_ic)
    return ic_series


# %%
df_backtest

# %%
#The multiprocessing takes forever, not sure why
#had to use for loop for now. Look into this later

# with pathos.multiprocessing.ProcessPool(pathos.helpers.cpu_count()) as pool: 
#     ic_series_results = pool.map( get_ic_series, COMBINE_FACTORS)
ic_series_results = [get_ic_series(factor).rename(factor) for factor in COMBINE_FACTORS] #around 8 seconds
df_ic_series = pd.concat(ic_series_results, axis=1)

# %%
df_ic_series

# %%
hist_periods = 12
df_ic_hist_mean = df_ic_series.rolling(hist_periods, min_periods=1).mean().iloc[hist_periods:]
df_ic_hist_mean

# %%
df_ic_cov_mat_series = df_ic_series.rolling(hist_periods, min_periods=1).cov().iloc[hist_periods * num_factors:]
df_ic_cov_mat_series

# %% [markdown]
# #### maximize the ICIR values on a single rebalancing date

# %%
date = '2012-01-31'

# %%
df_ic = df_ic_hist_mean.loc[date, :]
df_ic

# %%
df_ic_cov_mat = df_ic_cov_mat_series[df_ic_cov_mat_series.index.get_level_values(0) == date]
df_ic_cov_mat


# %%
def get_ic_ir(factor_weights):
    ic_mean = factor_weights.transpose() @ df_ic.values.flatten()
    ic_var = factor_weights @ df_ic_cov_mat.values @ factor_weights.transpose()
    return ic_mean / (ic_var ** 0.5)


# %%
uniform_weights = np.array([1 / n] * n)
get_ic_ir(uniform_weights)

# %%
opt_result = scipy.optimize.minimize(
                lambda w: -get_ic_ir(w),
                np.array([1 / n] * n),
                bounds=[(0, 1) for i in range(n)],
                constraints=({"type": "eq", 
                              "fun": lambda weight: np.sum(weight) - 1})
            )
opt_factor_weight = opt_result.x

# %%
opt_factor_weight

# %%
get_ic_ir(opt_factor_weight)

# %% [markdown]
# #### optimal factor weight on all rebalancing dates

# %%
df_ic_series

# %%
df_opt_factor_weights = pd.DataFrame([], columns=COMBINE_FACTORS + ['max_ICIR'])
num_factors = len(COMBINE_FACTORS)
for date in tqdm(df_ic_hist_mean.index):
    print(date)
    df_ic = df_ic_hist_mean.loc[date, :]
    df_ic_cov_mat = df_ic_cov_mat_series[df_ic_cov_mat_series.index.get_level_values(0) == date]
    uniform_weights = np.array([1 / num_factors] * num_factors)
    print(f"ICIR with uniform weights: {get_ic_ir(uniform_weights)}")

    opt_result = scipy.optimize.minimize(
                    lambda w: -get_ic_ir(w),
                    np.array([1 / num_factors] * num_factors),
                    bounds=[(0, 1) for i in range(num_factors)],
                    constraints=({"type": "eq", "fun": lambda weight: np.sum(weight) - 1})
                )
    def get_ic_ir(factor_weights):
        ic_mean = factor_weights.transpose() @ df_ic.values.flatten()
        ic_var = factor_weights @ df_ic_cov_mat.values @ factor_weights.transpose()
        return ic_mean / (ic_var ** 0.5)

    df_opt_factor_weights.loc[date, :] = list(opt_result.x) + [get_ic_ir(opt_result.x)]
    print(f"ICIR with optimal weights: {get_ic_ir(opt_factor_weight)}")
    get_ic_ir(opt_factor_weight)

# %%
df_opt_factor_weights

# %%
df_opt_factor_weight

# %%
results #TBD: not sure why the optimization is not working, debug

# %%
#we cannot use pandas.rolling.apply(func) because rolling.apply is different from groupby.apply -- it cannot take a dataframe as the parameter


# num_factors = len(COMBINE_FACTORS)
# def get_opt_factor_weight_by_date(ic_series: pd.Series): 
#     df = df_ic_series.loc[ic_series.index, :]
#     df_pred_ic = df.mean()
#     print('11111')
#     print(df_pred_ic)
#     df_pred_ic_cov_mat = df.cov()
#     print('a')
#     def get_ic_ir(factor_weights):
#         combined_ic_mean = factor_weights.transpose() @ df_pred_ic.values.flatten()
#         combined_ic_var = factor_weights @ df_pred_ic_cov_mat.values @ factor_weights.transpose()
#         combined_ir = combined_ic_mean / (combined_ic_var ** 0.5)
#         return combined_ir
#     print('b')
#     opt_result = scipy.optimize.minimize(
#                 lambda w: -get_ic_ir(w),
#                 np.array([1 / num_factors] * num_factors),
#                 bounds=[(0, 1) for i in range(num_factors)],
#                 constraints=({"type": "eq", "fun": lambda weight: np.sum(weight) - 1})
#             )
#     print('c')
#     date = df.index[0]
#     opt_factor_weight = pd.Series(opt_result.x, index=COMBINE_FACTORS, name=date)
#     print('d')
#     return opt_result.x

# df_ic_series.rolling(12).apply(get_opt_factor_weight_by_date)
