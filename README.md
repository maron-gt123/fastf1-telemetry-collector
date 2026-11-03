# fastf1-telemetry-collector

[FastF1](https://github.com/theOehrly/Fast-F1) を利用して、F1（Formula 1）のレースデータを取得・蓄積するPython製データ収集ツールです。

指定したシーズン、グランプリ、セッションのデータを取得し、データベースに保存することを目的としています。

## 機能

- FastF1を利用したF1データの取得
- 取得対象シーズンの指定
- 取得対象グランプリの指定
- 複数セッションの一括取得
- データベースへの取得データの蓄積
- `config.yaml` による各種設定の管理

## 動作環境

- Python 3.10 以上
- MySQL / MariaDB
- FastF1
- PyYAML
