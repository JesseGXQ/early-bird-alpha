import pytest

from examples.factor_quickstart import (
    LABEL_HORIZON,
    REBALANCE_INTERVAL,
    _print_summary,
    load_sample_dataset,
    run_quickstart,
)


EXPECTED_PRODUCTS = ('CU', 'AO', 'MA', 'LC', 'JD', 'EG', 'RM', 'B', 'PK', 'TF')
pytestmark = [pytest.mark.sample_data, pytest.mark.integration]


def test_sample_dataset_contract():
    dataset = load_sample_dataset()

    assert dataset.spec.products == EXPECTED_PRODUCTS
    assert dataset.main_panel.index.names == ['timestamp', 'product']
    assert dataset.main_panel.index.is_unique
    assert len(dataset.main_panel) == 4598
    assert dataset.quality.is_valid
    assert dataset.universe.eligible.index.equals(dataset.main_panel.index)


def test_factor_quickstart_runs_end_to_end(tmp_path):
    plot_path = tmp_path / 'factor_quickstart_nav.png'

    summary = run_quickstart(plot_output=plot_path)

    assert summary['products'] == EXPECTED_PRODUCTS
    assert summary['start_date'] == '2023-01-03'
    assert summary['end_date'] == '2024-12-31'
    assert summary['observations'] == 4598
    assert summary['label_horizon'] == LABEL_HORIZON
    assert summary['rebalance_interval'] == REBALANCE_INTERVAL
    assert summary['factor_coverage'] == pytest.approx(1.0)
    assert summary['joint_coverage'] == pytest.approx(0.988511, abs=1e-6)
    assert summary['ic_mean'] == pytest.approx(0.0193431, abs=1e-6)
    assert summary['rank_ic_mean'] == pytest.approx(0.0052248, abs=1e-6)
    assert summary['total_return'] == pytest.approx(0.173811, abs=1e-6)
    assert summary['cagr'] == pytest.approx(0.087206, abs=1e-6)
    assert summary['sharpe'] == pytest.approx(1.021578, abs=1e-6)
    assert summary['portfolio_warnings'] == ()
    assert plot_path.is_file()
    assert plot_path.stat().st_size > 0


def test_summary_reports_label_horizon_and_rebalance_interval(
        tmp_path, capsys):
    summary = run_quickstart(
        plot_output=tmp_path / 'factor_quickstart_nav.png')

    _print_summary(summary)

    output = capsys.readouterr().out
    assert f'Mean IC ({LABEL_HORIZON}D forward-return label)' in output
    assert f'Mean Rank IC ({LABEL_HORIZON}D forward-return label)' in output
    assert f'rebalance={REBALANCE_INTERVAL}D' in output
