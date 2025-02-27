from typing import Any

from torch.nn import (Conv2d, Sequential, Dropout2d, Upsample)
from torch.nn.init import xavier_uniform
from torch.nn.modules.activation import Sigmoid
from torch.nn import BCEWithLogitsLoss
from torch.nn.modules.pooling import MaxPool2d
from torch.optim import Adam

from torch import Tensor

import pytorch_lightning as pl

from torchmetrics import (MaxMetric, StructuralSimilarityIndexMeasure)

from apu.ml.torch.activations import Activation
from apu.ml.torch.util import merge


class DeepMoon(pl.LightningModule):

    def __init__(self,
                 number_of_filter,
                 filter_length,
                 lmbda=1e-6,
                 dim=256,
                 activation="relu",
                 dropout=.15,
                 lr=1e-5):
        super().__init__()

        # this line allows to access init params with 'self.hparams' attribute
        # it also ensures init params will be stored in ckpt
        self.save_hyperparameters(logger=True)

        self.criterion = BCEWithLogitsLoss()

        # use separate metric instance for train, val and test step
        # to ensure a proper reduction over the epoch
        self.train_acc = StructuralSimilarityIndexMeasure()
        self.val_acc = StructuralSimilarityIndexMeasure()
        self.test_acc = StructuralSimilarityIndexMeasure()
        
        # for logging best so far validation accuracy
        self.val_acc_best = MaxMetric()

        self.down_0 = Sequential(
            Conv2d(
                in_channels=1,  # gray image
                out_channels=self.hparams.number_of_filter,
                kernel_size=(self.hparams.filter_length,
                             self.hparams.filter_length),
                padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter),
            Conv2d(in_channels=self.hparams.number_of_filter,
                   out_channels=self.hparams.number_of_filter,
                   kernel_size=(self.hparams.filter_length,
                                self.hparams.filter_length),
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter))

        number_of_filter_2 = self.hparams.number_of_filter * 2
        self.down_1 = Sequential(
            Conv2d(in_channels=self.hparams.number_of_filter,
                   out_channels=number_of_filter_2,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_2),
            Conv2d(in_channels=number_of_filter_2,
                   out_channels=number_of_filter_2,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_2))

        number_of_filter_4 = self.hparams.number_of_filter * 4
        self.down_2 = Sequential(
            Conv2d(in_channels=number_of_filter_2,
                   out_channels=number_of_filter_4,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_4),
            Conv2d(in_channels=number_of_filter_4,
                   out_channels=number_of_filter_4,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_4),
        )

        self.base = Sequential(
            Conv2d(in_channels=number_of_filter_4,
                   out_channels=number_of_filter_4,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter),
            Conv2d(in_channels=number_of_filter_4,
                   out_channels=number_of_filter_4,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_4))

        self.down_0.apply(self.init_weights)
        self.down_1.apply(self.init_weights)
        self.down_2.apply(self.init_weights)
        self.base.apply(self.init_weights)

        self.up_1 = Sequential(
            Conv2d(in_channels=self.hparams.number_of_filter * 8,
                   out_channels=number_of_filter_2,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_2),
            Conv2d(in_channels=number_of_filter_2,
                   out_channels=number_of_filter_2,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, number_of_filter_2))

        self.up_2 = Sequential(
            Conv2d(in_channels=number_of_filter_4,
                   out_channels=self.hparams.number_of_filter,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter),
            Conv2d(in_channels=self.hparams.number_of_filter,
                   out_channels=self.hparams.number_of_filter,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter))

        self.up_3 = Sequential(
            Conv2d(in_channels=number_of_filter_2,
                   out_channels=self.hparams.number_of_filter,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter),
            Conv2d(in_channels=self.hparams.number_of_filter,
                   out_channels=self.hparams.number_of_filter,
                   kernel_size=self.hparams.filter_length,
                   padding=1),
            Activation(self.hparams.activation, self.hparams.number_of_filter))

        self.up_1.apply(self.init_weights)
        self.up_2.apply(self.init_weights)
        self.up_3.apply(self.init_weights)

        self.out_conv = Conv2d(in_channels=self.hparams.number_of_filter,
                               out_channels=1,
                               kernel_size=1)

        self.out_conv.apply(self.init_weights)

    def forward(self, idata):
        # down
        d0 = self.down_0(idata)
        max_1 = MaxPool2d(kernel_size=(2, 2), stride=(2, 2))(d0)
        d1 = self.down_1(max_1)
        max_2 = MaxPool2d(kernel_size=(2, 2), stride=(2, 2))(d1)
        d2 = self.down_2(max_2)
        max_3 = MaxPool2d(kernel_size=(2, 2), stride=(2, 2))(d2)

        # base
        u = self.base(max_3)
        u = Upsample(scale_factor=2, mode='nearest')(u)

        # upscale
        u = merge(layers=(d2, u), cat_axis=1)
        u = self.dropout_reg(u)
        u = self.up_1(u)
        u = Upsample(scale_factor=2, mode='nearest')(u)

        u = merge(layers=(d1, u), cat_axis=1)
        u = self.dropout_reg(u)
        u = self.up_2(u)
        u = Upsample(scale_factor=2, mode='nearest')(u)

        u = merge(layers=(d0, u), cat_axis=1)
        u = self.dropout_reg(u)
        u = self.up_3(u)

        u = self.out_conv(u)
        u = Sigmoid()(u)

        return u

    def dropout_reg(self, tensor: Tensor) -> Tensor:
        if self.hparams.dropout is not None and self.hparams.dropout > 0:
            return Dropout2d(p=self.hparams.dropout, inplace=True)(tensor)
        return tensor

    def init_weights(self, tensor:Tensor) -> None:
        if isinstance(tensor, Conv2d):
            xavier_uniform(tensor.weight)

    def configure_optimizers(self) -> Adam:
        return Adam(self.parameters(),
                         lr=self.hparams.lr,
                         weight_decay=self.hparams.lmbda)

    def step(self, batch: Any):
        '''
            make a prediction step.

            1. get the input data x
            2. get the output data y
            3. make the loss calculation
        '''
        x, y, _ = batch
        y_hat = self(x)
        loss = self.criterion(y_hat, y)
        return loss, y_hat, y

    def training_step(self, train_batch: Any, batch_idx: int) -> dict:
        # data to device
        loss, preds, targets = self.step(train_batch)

        # train accuracy
        acc = self.train_acc(preds, targets)

        self.log("train/loss",
                 loss.detach().item(),
                 on_step=True,
                 on_epoch=True,
                 prog_bar=True,
                 logger=True)

        self.log("train/acc", 
                  acc.detach().item(), 
                  on_step=True, 
                  on_epoch=True, 
                  prog_bar=True,
                  logger=True)

        return loss

    def validation_step(self, val_batch: Any, batch_idx: int) -> dict:
        loss, preds, targets = self.step(val_batch)

        # log val metrics
        acc = self.val_acc(preds, targets)
    
        self.log("val/loss",
                 loss.detach().item(),
                 on_step=True,
                 on_epoch=True,
                 prog_bar=True,
                 logger=True)
        
        self.log("val/acc", 
                  acc.detach().item(), 
                  on_step=True, 
                  on_epoch=True, 
                  prog_bar=True,
                 logger=True)
    
        return loss

    def on_validation_epoch_end(self):
        acc = self.val_acc.compute() 

        self.val_acc_best.update(acc)

        self.log("val/acc_best",
                 self.val_acc_best.compute().detach().item(),
                 on_epoch=True,
                 prog_bar=True,
                 logger=True)

    def test_step(self, batch: Any, batch_idx: int):
        loss, preds, targets = self.step(batch)

        # log test metrics
        acc = self.test_acc(preds, targets)

        self.log("test/loss", 
                 loss.detach().item(), 
                 on_step=True, 
                 on_epoch=True,
                 logger=True)

        self.log("test/acc", 
                 acc.detach().item(), 
                 on_step=True, 
                 on_epoch=True,
                 logger=True)

        return loss

    def on_epoch_end(self):
        # reset metrics at the end of every epoch
        self.train_acc.reset()
        self.test_acc.reset()
        self.val_acc.reset()

    def on_train_start(self):
        # reset metrics at the end of every epoch
        self.val_acc_best.reset()
