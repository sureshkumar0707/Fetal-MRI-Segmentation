import os
import glob

import fetal_net
import fetal_net.preprocess
import fetal_net.metrics
from brats.utils import get_last_model_path
from fetal_net.data import write_data_to_file, open_data_file
from fetal_net.generator import get_training_and_validation_generators
from fetal_net.model.fetal_net import fetal_envelope_model
from fetal_net.training import load_old_model, train_model
from pathlib import Path

import json
import argparse

import numpy as np

parser = argparse.ArgumentParser()

parser.add_argument("--overwrite_config", help="overwrite saved config",
                    action="store_true")
parser.add_argument("--config_dir", help="specifies config dir path",
                    type=str, required=True)
parser.add_argument("--split_dir", help="specifies config dir path",
                    type=str, required=False)
opts = parser.parse_args()

# Load previous config if exists
if Path(os.path.join(opts.config_dir, 'config.json')).exists() and not opts.overwrite_config:
    print('Loading previous config.json from {}'.format(opts.config_dir))
    with open(os.path.join(opts.config_dir, 'config.json')) as f:
        config = json.load(f)
else:
    config = dict()
    config["base_dir"] = opts.config_dir
    config["split_dir"] = './debug_split'
    config['scans_dir'] = '../../Datasets/brain_new_cutted_window_1_99'

    config["fake_split_dir"] = './fake_debug_split'
    config['fake_scans_dir'] = '../../Datasets/brain_new_cutted_window_1_99'

    Path(config["base_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["split_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["fake_split_dir"]).mkdir(parents=True, exist_ok=True)

    # Training params
    config["batch_size"] = 2
    config["validation_batch_size"] = 2  # most of times should be equal to "batch_size"
    config["patches_per_epoch"] = 100  # patches_per_epoch / batch_size = steps per epoch

    config["n_epochs"] = 50  # cutoff the training after this many epochs
    config[
        "patience"] = 3  # learning rate will be reduced after this many epochs if the validation loss is not improving
    config["early_stop"] = 7  # training will be stopped after this many epochs without the validation loss improving
    config["initial_learning_rate"] = 1e-4
    config["learning_rate_drop"] = 0.5  # factor by which the learning rate will be reduced
    config["validation_split"] = 0.90  # portion of the data that will be used for training %

    config["3D"] = False  # Enable for 3D Models
    if config["3D"]:
        # Model params (3D)
        config["patch_shape"] = (16, 16)  # switch to None to train on the whole image
        config["patch_depth"] = 16
        config["truth_index"] = 0
        config["truth_size"] = 16
        model_name = 'isensee'  # or 'unet'
    else:
        # Model params (2D) - should increase "batch_size" and "patches_per_epoch"
        config["patch_shape"] = (64, 64)  # switch to None to train on the whole image
        config["patch_depth"] = 5
        config["truth_index"] = 2
        config["truth_size"] = 1
        model_name = 'unet'  # or 'isensee'

    # choose model
    config["model_name"] = {
        '3D': {
            'unet': 'unet_model_3d',
            'isensee': 'isensee2017_model_3d'
        },
        '2D': {
            'unet': 'unet_model_2d',
            'isensee': 'isensee2017_model'
        }
    }['3D' if config["3D"] else '2D'][model_name]
    config["model_name"] = 'dis_style_net'

    # choose loss
    config["loss"] = {
        0: 'binary_crossentropy_loss',
        1: 'dice_coefficient_loss',
        2: 'focal_loss',
        3: 'dice_and_xent',
        4: 'dice_and_xent_mask'
    }[1]

    config["augment"] = {
        "flip": [0.5, 0.5, 0.5],  # augments the data by randomly flipping an axis during
        "permute": False,
        # NOT SUPPORTED (data shape must be a cube. Augments the data by permuting in various directions)
        "translate": (15, 15, 7),  #
        "scale": (0.1, 0.1, 0),  # i.e 0.20 for 20%, std of scaling factor, switch to None if you want no distortion
        # "iso_scale": {
        #     "max": 1
        # },
        "rotate": (0, 0, 90),  # std of angle rotation, switch to None if you want no rotation
        "poisson_noise": 1,
        "gaussian_filter": {
            "prob": 0.0,
            "max_sigma": 1
        },
        "contrast": {
            'prob': 0,
            'min_factor': 0.2,
            'max_factor': 0.1
        },
        # "piecewise_affine": {
        #     'scale': 2
        # },
        "elastic_transform": {
            'alpha': 5,
            'sigma': 10
        },
        # "intensity_multiplication": 0.2,
        "coarse_dropout": {
            "rate": 0.2,
            "size_percent": [0.10, 0.30],
            "per_channel": True
        },
        "gaussian_noise": {
            "prob": 0.5,
            "sigma": 0.05
        },
        "speckle_noise": {
            "prob": 0.5,
            "sigma": 0.05
        }
    }

    # If the model outputs smaller result (x,y)-wise than the input
    config["truth_downsample"] = None  # factor to downsample the ground-truth
    config["truth_crop"] = False  # if true will crop sample else resize
    config["categorical"] = False  # will make the target one_hot

    # Relevant only for previous slice truth training
    config["prev_truth_index"] = None  # None for regular training
    config["prev_truth_size"] = None  # None for regular training

    config["labels"] = (1,)  # the label numbers on the input image - currently only 1 label supported

    config["skip_blank_train"] = False  # if True, then patches without any target will be skipped
    config["skip_blank_val"] = False  # if True, then patches without any target will be skipped
    config["drop_easy_patches_train"] = False  # will randomly prefer balanced patches (50% 1, 50% 0)
    config["drop_easy_patches_val"] = False  # will randomly prefer balanced patches (50% 1, 50% 0)

    # Data normalization
    config['normalization'] = {
        0: False,
        1: 'all',
        2: 'each'
    }[1]  # Normalize by all or each data mean and std

    # add ".gz" extension if needed
    config["ext"] = ".gz"

    # Not relevant at the moment...
    config["dropout_rate"] = 0

    # Weight masks (currently supported only with isensee3d model and dice_and_xent_weigthed loss)
    config["weight_mask"] = None  # ["dists"] # or []

    # Auto set - do not touch
    config["augment"] = config["augment"] if any(config["augment"].values()) else None
    config["n_labels"] = len(config["labels"])
    config["all_modalities"] = ["volume"]
    config["training_modalities"] = config["all_modalities"]
    config["nb_channels"] = len(config["training_modalities"])
    config["truth_channel"] = config["nb_channels"]

    # Auto set - do not touch
    config["data_file"] = os.path.join(config["base_dir"], "fetal_data.h5")
    config["fake_data_file"] = os.path.join(config["base_dir"], "fake_fetal_data.h5")
    config["model_file"] = os.path.join(config["base_dir"], "fetal_net_model")
    config["overwrite"] = False  # If True, will previous files. If False, will use previously written files.
    config["scale_data"] = None  # (2, 2, 1)

    config["preproc"] = {
        0: "laplace",
        1: "laplace_norm",
        2: "grad",
        3: "grad_norm"
    }[1]
    config["preproc"] = None

    # relevant only to NormNet
    config["old_model"] = '/home/galdude33/Lab/workspace/fetal_envelope2/brats/debug_normnet/old_model.h5'

    with open(os.path.join(config["base_dir"], 'config.json'), mode='w') as f:
        json.dump(config, f, indent=2)

config["training_file"] = os.path.join(config["split_dir"], "training_ids.pkl")
config["validation_file"] = os.path.join(config["split_dir"], "validation_ids.pkl")
config["test_file"] = os.path.join(config["split_dir"], "test_ids.pkl")

config["fake_training_file"] = os.path.join(config["fake_split_dir"], "fake_training_ids.pkl")
config["fake_validation_file"] = os.path.join(config["fake_split_dir"], "fake_validation_ids.pkl")
config["fake_test_file"] = os.path.join(config["fake_split_dir"], "fake_test_ids.pkl")

config["input_shape"] = tuple(list(config["patch_shape"]) +
                              [config["patch_depth"] + (
                                  config["prev_truth_size"] if config["prev_truth_index"] is not None else 0)])
if config['3D']:
    config["input_shape"] = [1] + list(config["input_shape"])


def fetch_training_data_files(modalities, scans_dir, return_subject_ids=False):
    training_data_files = list()
    subject_ids = list()
    # for subject_dir in glob.glob(os.path.join(os.path.dirname(__file__), "data", "preprocessed", "*", "*")):
    for subject_dir in sorted(glob.glob(os.path.join(scans_dir, "*")),
                              key=os.path.basename):
        subject_ids.append(os.path.basename(subject_dir))
        subject_files = list()
        for modality in modalities:
            subject_files.append(os.path.join(subject_dir, modality + ".nii" + config["ext"]))
        training_data_files.append(tuple(subject_files))
    if return_subject_ids:
        return training_data_files, subject_ids
    else:
        return training_data_files


def main(overwrite=False):
    training_files, subject_ids = \
        fetch_training_data_files(modalities=config["training_modalities"] + ["truth"] + (
            config["weight_mask"] if config["weight_mask"] is not None else []),
                                  scans_dir=config['scans_dir'],
                                  return_subject_ids=True)
    data_file_opened = get_data_file(training_files, subject_ids, overwrite,
                                     data_file=config['data_file'],
                                     base_dir=config['base_dir'],
                                     preproc=config['preproc'],
                                     normalization=config['normalization'],
                                     scale_data=config['scale_data'],
                                     name='main')

    fake_training_files, fake_subject_ids = \
        fetch_training_data_files(modalities=['volume'],
                                  scans_dir=config['fake_scans_dir'],
                                  return_subject_ids=True)
    fake_data_file_opened = get_data_file(fake_training_files, fake_subject_ids, overwrite,
                                          data_file=config['fake_data_file'],
                                          base_dir=config['base_dir'],
                                          preproc=config['preproc'],
                                          normalization=config['normalization'],
                                          scale_data=config['scale_data'],
                                          name='fake')

    # instantiate new model
    loss_func = getattr(fetal_net.metrics, config['loss'])
    model_func = getattr(fetal_net.model, config['model_name'])
    model = model_func(input_shape=config["input_shape"],
                       initial_learning_rate=config["initial_learning_rate"],
                       **{'dropout_rate': config['dropout_rate'],
                          'loss_function': loss_func,
                          'mask_shape': None if config["weight_mask"] is None else config["input_shape"],
                          # TODO: change to output shape
                          'old_model_path': config['old_model']})
    if not overwrite and len(glob.glob(config["model_file"] + '*.h5')) > 0:
        model_path = get_last_model_path(config["model_file"])
        print('Loading model from: {}'.format(model_path))
        model.load_weights(model_path)
    model.summary()

    # get training and testing generators
    train_generator, validation_generator, n_train_steps, n_validation_steps = get_training_and_validation_generators(
        data_file_opened,
        batch_size=config["batch_size"],
        data_split=config["validation_split"],
        overwrite=overwrite,
        validation_keys_file=config["validation_file"],
        training_keys_file=config["training_file"],
        test_keys_file=config["test_file"],
        n_labels=config["n_labels"],
        labels=config["labels"],
        patch_shape=(*config["patch_shape"], config["patch_depth"]),
        validation_batch_size=config["validation_batch_size"],
        augment=config["augment"],
        skip_blank_train=config["skip_blank_train"],
        skip_blank_val=config["skip_blank_val"],
        truth_index=config["truth_index"],
        truth_size=config["truth_size"],
        prev_truth_index=config["prev_truth_index"],
        prev_truth_size=config["prev_truth_size"],
        truth_downsample=config["truth_downsample"],
        truth_crop=config["truth_crop"],
        patches_per_epoch=config["patches_per_epoch"],
        categorical=config["categorical"], is3d=config["3D"],
        drop_easy_patches_train=config["drop_easy_patches_train"],
        drop_easy_patches_val=config["drop_easy_patches_val"])

    get_fake_generator = lambda: get_training_and_validation_generators(
        fake_data_file_opened,
        batch_size=config["batch_size"],
        data_split=1.0,
        overwrite=overwrite,
        validation_keys_file=config["fake_validation_file"],
        training_keys_file=config["fake_training_file"],
        test_keys_file=config["fake_test_file"],
        n_labels=config["n_labels"],
        labels=config["labels"],
        patch_shape=(*config["patch_shape"], config["patch_depth"]),
        validation_batch_size=config["validation_batch_size"],
        augment=config["augment"],
        skip_blank_train=config["skip_blank_train"],
        skip_blank_val=config["skip_blank_val"],
        truth_crop=config["truth_crop"],
        patches_per_epoch=config["patches_per_epoch"],
        categorical=config["categorical"], is3d=config["3D"],
        drop_easy_patches_train=config["drop_easy_patches_train"],
        drop_easy_patches_val=config["drop_easy_patches_val"])

    fake_generator_train, _, _, _ = get_fake_generator()
    fake_generator_val, _, _, _ = get_fake_generator()

    def merge_gen(real_gen, fake_gen):
        while True:
            data, truth = real_gen.__next__()
            fake_data, _ = fake_gen.__next__()
            yield [data, fake_data], [truth, np.ones([len(data)])] #, np.zeros([len(data)])]

    total_train_gen = merge_gen(train_generator, fake_generator_train)
    total_val_gen = merge_gen(validation_generator, fake_generator_val)

    # run training
    train_model(model=model,
                model_file=config["model_file"],
                training_generator=total_train_gen,
                validation_generator=total_val_gen,
                steps_per_epoch=n_train_steps,
                validation_steps=n_validation_steps,
                initial_learning_rate=config["initial_learning_rate"],
                learning_rate_drop=config["learning_rate_drop"],
                learning_rate_patience=config["patience"],
                early_stopping_patience=config["early_stop"],
                n_epochs=config["n_epochs"],
                output_folder=config["base_dir"])
    data_file_opened.close()


def get_data_file(training_files, subject_ids, overwrite, data_file, base_dir, preproc=None, normalization=None,
                  scale_data=None, name=None):
    # convert input images into an hdf5 file
    if overwrite or not os.path.exists(data_file):
        preproc_func = None
        if preproc is not None:
            preproc_func = getattr(fetal_net.preprocess, preproc)

        _, (mean, std) = write_data_to_file(training_files, data_file, subject_ids=subject_ids,
                                            normalize=normalization, scale=scale_data,
                                            preproc=preproc_func)
        with open(os.path.join(base_dir, 'norm_params_{}.json'.format(name)), mode='w') as f:
            json.dump({'mean': mean, 'std': std}, f)
    data_file_opened = open_data_file(data_file)
    return data_file_opened


if __name__ == "__main__":
    main(overwrite=config["overwrite"])