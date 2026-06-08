# Ocean Multi-Agent RAG Evaluation Report

## Summary

- backend: `local`
- top_k: `6`
- commit: `25de387`
- samples: `10`
- Recall@K: **0.7417**
- Precision@K: **0.35**
- MRR: **0.725**
- nDCG@K: **N/A**
- citation coverage: **1.0**
- trace completeness: **1.0**
- avg latency: **86.71 ms**

## Per-Question Details

### q1

Question: How do marine heatwaves affect coral reefs and fisheries, and what adaptation actions are useful?

- Recall@K=1.0 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | mhw-global-warming | mhw-global-warming | 0.4578 | https://www.nature.com/articles/s41586-018-0383-9 |
| 2 | mhw-biodiversity | mhw-biodiversity | 0.4578 | https://www.nature.com/articles/s41558-019-0412-1 |
| 3 | md-marine_heatwave_rag_seed | md-marine_heatwave_rag_seed-c001 | 0.426 | C:\Users\lmh\Desktop\海洋rag+多agent\data\knowledge_docs\marine_heatwave_rag_seed.m |
| 4 | noaa-fisheries-oa | noaa-fisheries-oa | 0.2588 | https://www.fisheries.noaa.gov/insight/understanding-ocean-acidification |
| 5 | pdf-20_ibi_ocean_heat_content | pdf-20_ibi_ocean_heat_content | 0.1274 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\20_ibi_ocean_heat_content. |
| 6 | pdf-28_global_ocean_heat_content | pdf-28_global_ocean_heat_content | 0.1274 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\28_global_ocean_heat_conte |

### q2

Question: How does ocean acidification affect shellfish aquaculture and food security?

- Recall@K=1.0 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | noaa-fisheries-oa | noaa-fisheries-oa | 0.6424 | https://www.fisheries.noaa.gov/insight/understanding-ocean-acidification |
| 2 | md-ocean_acidification_rag_seed | md-ocean_acidification_rag_seed-c001 | 0.6089 | C:\Users\lmh\Desktop\海洋rag+多agent\data\knowledge_docs\ocean_acidification_rag_se |
| 3 | noaa-oa | noaa-oa | 0.3934 | https://oceanacidification.noaa.gov/what-is-ocean-acidification/ |
| 4 | mhw-biodiversity | mhw-biodiversity | 0.103 | https://www.nature.com/articles/s41558-019-0412-1 |
| 5 | ipcc-srocc | ipcc-srocc | 0.103 | https://www.ipcc.ch/srocc/chapter/summary-for-policymakers/ |
| 6 | pdf-20_ibi_ocean_heat_content | pdf-20_ibi_ocean_heat_content | 0.091 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\20_ibi_ocean_heat_content. |

### q3

Question: What climate risks and adaptation governance pathways are associated with sea-level rise?

- Recall@K=1.0 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | ipcc-srocc | ipcc-srocc | 0.459 | https://www.ipcc.ch/srocc/chapter/summary-for-policymakers/ |
| 2 | pdf-07_in_situ_sea_level | pdf-07_in_situ_sea_level | 0.2785 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\07_in_situ_sea_level.pdf |
| 3 | pdf-2019年中国海平面公报_b76facc7 | pdf-2019年中国海平面公报_b76facc7 | 0.1544 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2019年中国海平面公报_b76facc7.pdf |
| 4 | pdf-2020年中国海平面公报_d79a1558 | pdf-2020年中国海平面公报_d79a1558 | 0.1544 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2020年中国海平面公报_d79a1558.pdf |
| 5 | pdf-06_baltic_north_sea_sst | pdf-06_baltic_north_sea_sst | 0.1004 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\06_baltic_north_sea_sst.pd |
| 6 | pdf-41_baltic_north_sea_sst_nrt | pdf-41_baltic_north_sea_sst_nrt | 0.0996 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\41_baltic_north_sea_sst_nr |

### q4

Question: What do recent China coastal and marine ecological environment bulletins say about coastal water quality?

- Recall@K=0.0 | Precision@K=0.0 | MRR=0.0 | nDCG@K=None
- missed gold documents: pdf-2023年中国海洋生态环境状况公报_236f8ebc, pdf-2021年中国海洋生态环境状况公报_718d3939, pdf-2018年中国海洋生态环境状况公报_2bfff7c8
- missed gold chunks: pdf-2023年中国海洋生态环境状况公报_236f8ebc, pdf-2021年中国海洋生态环境状况公报_718d3939, pdf-2018年中国海洋生态环境状况公报_2bfff7c8

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | pdf-2009年中国近岸海域环境质量公报_aadf0abf | pdf-2009年中国近岸海域环境质量公报_aadf0abf | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2009年中国近岸海域环境质量公报_aadf0abf |
| 2 | pdf-2010年中国近岸海域环境质量公报_93de906e | pdf-2010年中国近岸海域环境质量公报_93de906e | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2010年中国近岸海域环境质量公报_93de906e |
| 3 | pdf-2011年中国近岸海域环境质量公报_485863d1 | pdf-2011年中国近岸海域环境质量公报_485863d1 | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2011年中国近岸海域环境质量公报_485863d1 |
| 4 | pdf-2012年中国近岸海域环境质量公报_f0e879c4 | pdf-2012年中国近岸海域环境质量公报_f0e879c4 | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2012年中国近岸海域环境质量公报_f0e879c4 |
| 5 | pdf-2014年中国近岸海域环境质量公报_d7cd2eb4 | pdf-2014年中国近岸海域环境质量公报_d7cd2eb4 | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2014年中国近岸海域环境质量公报_d7cd2eb4 |
| 6 | pdf-2015年中国近岸海域环境质量公报_4b2bb7e8 | pdf-2015年中国近岸海域环境质量公报_4b2bb7e8 | 0.367 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\cn\2015年中国近岸海域环境质量公报_4b2bb7e8 |

### q5

Question: What are the main risks from marine disasters such as storm surge and waves?

- Recall@K=0.0 | Precision@K=0.0 | MRR=0.0 | nDCG@K=None
- missed gold documents: pdf-2020年中国海洋灾害公报_6e47b2e7, pdf-2020年福建省海洋灾害公报_e75caebe, pdf-2022年福建省海洋灾害公报_69a2428d
- missed gold chunks: pdf-2020年中国海洋灾害公报_6e47b2e7, pdf-2020年福建省海洋灾害公报_e75caebe, pdf-2022年福建省海洋灾害公报_69a2428d

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | pdf-17_significant_wave_height_extreme_variability | pdf-17_significant_wave_height_extreme_variability | 0.2243 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\17_significant_wave_height |
| 2 | mhw-biodiversity | mhw-biodiversity | 0.1302 | https://www.nature.com/articles/s41558-019-0412-1 |
| 3 | pdf-23_ibi_wave_forecast | pdf-23_ibi_wave_forecast | 0.1269 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\23_ibi_wave_forecast.pdf |
| 4 | pdf-09_global_waves_forecast | pdf-09_global_waves_forecast | 0.1089 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\09_global_waves_forecast.p |
| 5 | pdf-42_mediterranean_wave_forecast | pdf-42_mediterranean_wave_forecast | 0.1244 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\42_mediterranean_wave_fore |
| 6 | pdf-44_black_sea_wave_forecast | pdf-44_black_sea_wave_forecast | 0.1213 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\44_black_sea_wave_forecast |

### q6

Question: Under global warming, what threats do marine biodiversity and ecosystem services face?

- Recall@K=1.0 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | mhw-biodiversity | mhw-biodiversity | 0.8376 | https://www.nature.com/articles/s41558-019-0412-1 |
| 2 | mhw-global-warming | mhw-global-warming | 0.5243 | https://www.nature.com/articles/s41586-018-0383-9 |
| 3 | md-marine_heatwave_rag_seed | md-marine_heatwave_rag_seed-c001 | 0.4433 | C:\Users\lmh\Desktop\海洋rag+多agent\data\knowledge_docs\marine_heatwave_rag_seed.m |
| 4 | md-ocean_acidification_rag_seed | md-ocean_acidification_rag_seed-c001 | 0.1467 | C:\Users\lmh\Desktop\海洋rag+多agent\data\knowledge_docs\ocean_acidification_rag_se |
| 5 | noaa-oa | noaa-oa | 0.109 | https://oceanacidification.noaa.gov/what-is-ocean-acidification/ |
| 6 | ipcc-srocc | ipcc-srocc | 0.109 | https://www.ipcc.ch/srocc/chapter/summary-for-policymakers/ |

### q7

Question: What are the main IPCC SROCC conclusions and governance recommendations for ocean and cryosphere change?

- Recall@K=1.0 | Precision@K=0.1667 | MRR=0.25 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | md-ocean_acidification_rag_seed | md-ocean_acidification_rag_seed-c001 | 0.3325 | C:\Users\lmh\Desktop\海洋rag+多agent\data\knowledge_docs\ocean_acidification_rag_se |
| 2 | noaa-oa | noaa-oa | 0.278 | https://oceanacidification.noaa.gov/what-is-ocean-acidification/ |
| 3 | noaa-fisheries-oa | noaa-fisheries-oa | 0.2515 | https://www.fisheries.noaa.gov/insight/understanding-ocean-acidification |
| 4 | ipcc-srocc | ipcc-srocc | 0.2123 | https://www.ipcc.ch/srocc/chapter/summary-for-policymakers/ |
| 5 | pdf-04_global_ocean_reanalysis_phy | pdf-04_global_ocean_reanalysis_phy | 0.1041 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\04_global_ocean_reanalysis |
| 6 | pdf-46_mediterranean_ocean_salt_content | pdf-46_mediterranean_ocean_salt_content | 0.0997 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\46_mediterranean_ocean_sal |

### q8

Question: Which products monitor sea surface temperature (SST) and extreme temperature variability?

- Recall@K=0.6667 | Precision@K=0.3333 | MRR=1.0 | nDCG@K=None
- missed gold documents: pdf-49_global_sst_l4_reprocessed
- missed gold chunks: pdf-49_global_sst_l4_reprocessed

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | pdf-06_baltic_north_sea_sst | pdf-06_baltic_north_sea_sst | 0.4485 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\06_baltic_north_sea_sst.pd |
| 2 | pdf-41_baltic_north_sea_sst_nrt | pdf-41_baltic_north_sea_sst_nrt | 0.4485 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\41_baltic_north_sea_sst_nr |
| 3 | pdf-14_sea_temperature_extreme_variability | pdf-14_sea_temperature_extreme_variability | 0.3441 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\14_sea_temperature_extreme |
| 4 | pdf-33_atlantic_sst_nrt | pdf-33_atlantic_sst_nrt | 0.3934 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\33_atlantic_sst_nrt.pdf |
| 5 | pdf-37_atlantic_sst_reprocessed | pdf-37_atlantic_sst_reprocessed | 0.3934 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\37_atlantic_sst_reprocesse |
| 6 | pdf-45_mediterranean_sst_indicators | pdf-45_mediterranean_sst_indicators | 0.3934 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\45_mediterranean_sst_indic |

### q9

Question: Which data products are relevant for significant wave height extremes and coastal strong-wave events?

- Recall@K=1.0 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: none
- missed gold chunks: none

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | pdf-17_significant_wave_height_extreme_variability | pdf-17_significant_wave_height_extreme_variability | 0.2847 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\17_significant_wave_height |
| 2 | pdf-12_ibi_strong_wave_incidence | pdf-12_ibi_strong_wave_incidence | 0.1638 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\12_ibi_strong_wave_inciden |
| 3 | pdf-23_ibi_wave_forecast | pdf-23_ibi_wave_forecast | 0.1463 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\23_ibi_wave_forecast.pdf |
| 4 | pdf-42_mediterranean_wave_forecast | pdf-42_mediterranean_wave_forecast | 0.1447 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\42_mediterranean_wave_fore |
| 5 | pdf-44_black_sea_wave_forecast | pdf-44_black_sea_wave_forecast | 0.1403 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\44_black_sea_wave_forecast |
| 6 | pdf-47_northwest_shelf_wave_forecast | pdf-47_northwest_shelf_wave_forecast | 0.1292 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\47_northwest_shelf_wave_fo |

### q10

Question: Which ocean carbon observation and biogeochemical (BGC) products are available?

- Recall@K=0.75 | Precision@K=0.5 | MRR=1.0 | nDCG@K=None
- missed gold documents: pdf-02_in_situ_carbon_observations
- missed gold chunks: pdf-02_in_situ_carbon_observations

| rank | doc_id | chunk_id | score | source |
|---|---|---|---|---|
| 1 | pdf-10_baltic_sea_physical___bgc | pdf-10_baltic_sea_physical___bgc | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\10_baltic_sea_physical___b |
| 2 | pdf-13_arctic_phy_ice_bgc | pdf-13_arctic_phy_ice_bgc | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\13_arctic_phy_ice_bgc.pdf |
| 3 | pdf-16_global_bgc_lmtl_biomass | pdf-16_global_bgc_lmtl_biomass | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\16_global_bgc_lmtl_biomass |
| 4 | pdf-18_ibi_bgc_forecast | pdf-18_ibi_bgc_forecast | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\18_ibi_bgc_forecast.pdf |
| 5 | pdf-24_ibi_multiyear_bgc | pdf-24_ibi_multiyear_bgc | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\24_ibi_multiyear_bgc.pdf |
| 6 | pdf-25_global_bgc_multiyear_hindcast | pdf-25_global_bgc_multiyear_hindcast | 0.5857 | C:\Users\lmh\Desktop\海洋rag+多agent\data\pdf_reports\en\25_global_bgc_multiyear_hi |
