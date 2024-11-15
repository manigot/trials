import pandas as pd
import torch as th
import typer
from loguru import logger
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.utils import set_random_seed
from train_trials import build_dataset, load_data, select_file_name

import wandb
from env import ReinforceTradingEnv


def script(args):
    """
    Generate trading behavior.

    Args:
    pairs_path: The files generated by select_pairs.py to store
               the data of the two stocks contained in each pair
               are stored in pairs_path.
    store_path_dir: The generated trading behavior files are
                    stored in store_path_dir.
    trading_threshold: The value of trading_threshold.
    stop_loss_threshold: The value of stop_loss_threshold.
    Returns:
    Noting to see here.
    """

    coin_pairs = pd.read_csv(args.selected_symbol_path)
    coin_pairs = coin_pairs["pairs"].values.tolist()
    coin_pairs = list(
        map(lambda x: x[x.rfind("_") + 1 :].split("-"), coin_pairs)
    )
    set_random_seed(args.seed, using_cuda=True)
    th.set_num_threads(args.num_process)
    logger.info(f"Start training for {args.rolling_serial}")
    logger.info(f"Load data from {args.rolling_dataset_path}")
    train = select_file_name(args.rolling_dataset_path, "train")
    valid = select_file_name(args.rolling_dataset_path, "valid")
    test = select_file_name(args.rolling_dataset_path, "test")
    assert args.rolling_serial < len(train)
    df_train = load_data(args.rolling_dataset_path, train[args.rolling_serial])
    df_valid = load_data(args.rolling_dataset_path, valid[args.rolling_serial])
    df_test = load_data(args.rolling_dataset_path, test[args.rolling_serial])

    asset_names, train_dataset, valid_dataset, test_dataset = build_dataset(
        df_train, df_valid, df_test, args.asset_num, args.feature_dim
    )

    def log_dataset(name, dataset):
        logger.info(
            f"Generated {name} dataset:\n  "
            f"Formation ({dataset[4][0]} - {dataset[4][-1]})\n  "
            f"Data size (N x T x M): {dataset[0].shape}\n  "
            f"Trading ({dataset[5][0]} - {dataset[5][-1]})\n  "
            f"Data size (N x T x M): {dataset[2].shape}"
        )

    dataset_names = ["train", "valid", "test"]
    datasets = [train_dataset, valid_dataset, test_dataset]
    [
        log_dataset(dataset_names[index], dataset)
        for index, dataset in enumerate(datasets)
    ]
    serial_selection = args.policy in ["simple_serial_selection"]
    action = (
        asset_names.index(coin_pairs[args.rolling_serial][0]),
        asset_names.index(coin_pairs[args.rolling_serial][1]),
    )

    def initialize_env(
        name,
        names,
        dataset,
        feature_dim,
        serial_selection,
        asset_attention,
        trading_train_steps,
        model,
    ):
        return Monitor(
            ReinforceTradingEnv(
                name=name,
                form_date=dataset[4],
                trad_date=dataset[5],
                asset_name=names,
                form_asset_features=dataset[0],
                form_asset_log_prices=dataset[1],
                trad_asset_features=dataset[2],
                trad_asset_log_prices=dataset[3],
                feature_dim=feature_dim,
                serial_selection=serial_selection,
                asset_attention=asset_attention,
                num_process=args.num_process,
                trading_feature_extractor=args.trading_feature_extractor,
                trading_feature_extractor_feature_dim=args.trading_feature_extractor_feature_dim,
                trading_feature_extractor_num_layers=args.trading_feature_extractor_num_layers,
                trading_feature_extractor_hidden_dim=args.trading_feature_extractor_hidden_dim,
                trading_feature_extractor_num_heads=args.trading_feature_extractor_num_heads,
                trading_train_steps=trading_train_steps,
                trading_num_process=args.trading_num_process,
                trading_dropout=args.trading_dropout,
                policy=args.policy,
                trading_learning_rate=args.trading_learning_rate,
                trading_log_dir=args.trading_log_dir,
                trading_rl_gamma=args.trading_rl_gamma,
                trading_ent_coef=args.trading_ent_coef,
                seed=args.seed,
                model=model,
            )
        )

    train_env = initialize_env(
        "train",
        asset_names,
        train_dataset,
        args.feature_dim,
        serial_selection,
        args.asset_attention,
        args.trading_train_steps,
        None,
    )

    test_env = initialize_env(
        "test",
        asset_names,
        test_dataset,
        args.feature_dim,
        serial_selection,
        args.asset_attention,
        0,
        train_env.model,
    )
    train_obs = train_env.reset()
    train_obs, train_reward, train_done, train_info = train_env.step(action)
    test_obs = test_env.reset()

    test_env.is_eval = True
    obs, reward, done, info = test_env.step(action)
    test_env.is_eval = False
    x_index, y_index = test_env.get_map_action(action)
    figure = test_env.plot_trajectory(
        test_env.trad_date,
        [
            test_env.asset_name[asset_index]
            for asset_index in [x_index, y_index]
        ],
        test_env.trad_asset_log_prices[x_index, :],
        test_env.trad_asset_log_prices[y_index, :],
        info["actions"],
        info["returns"],
    )
    wandb_dict = {
        f"{test_env.name}/final_reward": reward,
    }
    wandb_dict[f"{test_env.name}/final_sharpe_ratio"] = info["sharpe_ratio"]
    wandb_dict[f"{test_env.name}/final_annual_return"] = info["annual_return"]
    wandb_dict[f"{test_env.name}/final_annual_volatility"] = info[
        "annual_volatility"
    ]
    wandb_dict[f"{test_env.name}/final_max_drawdown"] = info["max_drawdown"]
    wandb_dict[f"{test_env.name}/trajectory_figure"] = wandb.Image(figure)
    wandb.log(
        wandb_dict,
        commit=True,
    )


def main(
    log_dir: str = "log",
    saved_model_dir: str = "saved_model",
    rolling_dataset_path: str = "trials/data/",
    policy: str = "simple_serial_selection",
    feature_extractor: str = "mlp",
    trading_feature_extractor: str = "lstm",
    asset_attention: bool = False,
    rolling_serial: int = 1,
    asset_num: int = 60,
    feature_dim: int = 3,
    feature_extractor_hidden_dim: int = 64,
    feature_extractor_num_layers: int = 1,
    feature_extractor_num_heads: int = 2,
    policy_network_hidden_dim: int = 64,
    seed: int = 13,
    patience_steps: int = 0,
    eval_freq: int = 32,
    train_steps: int = 1e4,
    learning_rate: float = 1e-4,
    dropout: float = 0.5,
    rl_gamma: float = 1,
    ent_coef: float = 1e-4,
    num_process: int = 1,
    project: str = "learning_to_pair",
    entity: str = "jimin",
    trading_train_steps: int = 1e3,
    trading_feature_extractor_feature_dim: int = 3,
    trading_feature_extractor_num_layers: int = 1,
    trading_feature_extractor_hidden_dim: int = 64,
    trading_dropout: float = 0.5,
    trading_feature_extractor_num_heads: int = 2,
    trading_learning_rate: float = 1e-4,
    trading_log_dir: str = "trading_log",
    trading_rl_gamma: float = 1,
    trading_ent_coef: float = 1e-4,
    trading_num_process: int = 2,
    selected_symbol_path: str = "trials/scripts/script/coin_pairs.csv",
) -> None:
    """
    Train l2r and its ablations

    Args:
    log_dir: the directory to save logs
    saved_model_dir: the directory to save models
    rolling_dataset_path: All rolling datasets are stored in
                          rolling_dataset_path.
    rolling_serial: the rolling to train
    asset_num: the number of assets
    feature_dim: the size of features
    feature_extractor_hidden_dim: the dim of the hidden layer in feature
    extractor
    policy_network_hidden_dim: the dim of the hidden layer in policy network
    seed: the random seed
    patience_steps: the steps before stop running for poor performance
    eval_freq: evaluation per steps
    train_steps: the total training steps


    Returns:
    Nothing to see here.
    """
    args = dict(
        log_dir=log_dir,
        saved_model_dir=saved_model_dir,
        rolling_dataset_path=rolling_dataset_path,
        policy=policy,
        feature_extractor=feature_extractor,
        trading_feature_extractor=trading_feature_extractor,
        asset_attention=asset_attention,
        rolling_serial=rolling_serial,
        asset_num=asset_num,
        feature_dim=feature_dim,
        feature_extractor_hidden_dim=feature_extractor_hidden_dim,
        feature_extractor_num_layers=feature_extractor_num_layers,
        feature_extractor_num_heads=feature_extractor_num_heads,
        policy_network_hidden_dim=policy_network_hidden_dim,
        seed=seed,
        patience_steps=patience_steps,
        eval_freq=eval_freq,
        train_steps=train_steps,
        learning_rate=learning_rate,
        dropout=dropout,
        rl_gamma=rl_gamma,
        ent_coef=ent_coef,
        num_process=num_process,
        project=project,
        entity=entity,
        trading_train_steps=trading_train_steps,
        trading_feature_extractor_feature_dim=trading_feature_extractor_feature_dim,
        trading_feature_extractor_num_layers=trading_feature_extractor_num_layers,
        trading_dropout=trading_dropout,
        trading_feature_extractor_hidden_dim=trading_feature_extractor_hidden_dim,
        trading_feature_extractor_num_heads=trading_feature_extractor_num_heads,
        trading_learning_rate=trading_learning_rate,
        trading_log_dir=trading_log_dir,
        trading_rl_gamma=trading_rl_gamma,
        trading_ent_coef=trading_ent_coef,
        trading_num_process=trading_num_process,
        selected_symbol_path=selected_symbol_path,
    )
    run = wandb.init(
        config=args,
        sync_tensorboard=False,
        monitor_gym=False,
        dir="/data/huangjimin",
    )
    script(wandb.config)


if __name__ == "__main__":
    typer.run(main)
