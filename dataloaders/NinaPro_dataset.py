from torch.utils.data import Dataset
import torch.nn.functional as F
from scipy.io import loadmat
from scipy import signal
import matplotlib.pyplot as plt
from scipy.fftpack import fft, ifft
import numpy as np
import random
import torch
import os
import re


class NinaProDataset(Dataset):
    def __init__(self, root=None, databases=['DB1'], experiments=['E1', 'E2', 'E3'], split='train',
                 random_sample=1024, window_length=128, overlap=0.6, transform=None, butterWn=None):
        super(NinaProDataset)
        # load parameters
        self.databases = databases
        self.experiments = experiments
        self.window_length = window_length
        self.overlap = overlap
        self.split = split
        self.transform = transform
        self.data = None
        self.label = None
        self.base_class_num = {'E1': 0, 'E2': 12, 'E3': 17 + 12}

        # choose database
        for database in self.databases:
            self.root = os.path.join(root, database)
            if not os.path.exists(self.root):
                raise RuntimeError('The file path did not exist.')
            # traverse patients
            sub_file_names = os.listdir(self.root)
            for name in sub_file_names:
                sample_name = re.findall(r"_(s\d+)", name)[0]
                for experiment in self.experiments:
                    if database == 'DB1':
                        path = os.path.join(self.root, name, name, sample_name+'_A1_' + experiment + '.mat')
                    else:
                        path = os.path.join(self.root, name, sample_name + '_{}_A1.mat'.format(experiment))
                    matlab_variable_dict = loadmat(path)
                    data = matlab_variable_dict['emg']
                    label = matlab_variable_dict['restimulus']
                    label[np.where(label != 0)] += self.base_class_num[experiment]
                    if self.data is None:
                        self.data = data
                        self.label = label
                    else:
                        self.data = np.vstack((self.data, data))
                        self.label = np.vstack((self.label, label))

        # 巴特沃斯滤波处理
        if butterWn is not None:
            b, a = signal.butter(N=2, Wn=butterWn)
            self.data = signal.filtfilt(b, a, self.data, axis=0)

        self.mean = np.mean(self.data, axis=0)
        self.std = np.std(self.data, axis=0)
        self.class_num = np.max(self.label) + 1  # take label 0 into account
        # segment the signals from label
        self.parsed_label = self.parse_label()
        # # 便于极大极小值归一化
        self.min_signal, self.max_signal = self.min_max_signal()
        # self.min_max_scalar()

        # 训练集测试集划分
        if self.split == 'train':
            self.length = random_sample
        elif self.split == 'valid':  # 将所有的信号段拼接在一起，便于getitem按索引读入
            self.valid_label_seg = []
            self.valid_label = []
            for i in range(len(self.parsed_label)):
                for seg in self.parsed_label[i]:
                    self.valid_label_seg.append(seg)
                    self.valid_label.append(i)
            self.length = len(self.valid_label_seg)
        else:
            self.length = random_sample

    def __len__(self):
        return self.length

    def __getitem__(self, item):
        if item >= self.__len__():
            raise IndexError
        # 测试的时候顺序遍历所有的数据
        if self.split == 'valid':
            seg_begin, seg_end = self.valid_label_seg[item]
            label = np.zeros(1, dtype=float)
            label[0] = self.valid_label[item]
        else:
            # set img offset
            label_id = random.randint(0, self.class_num - 1)
            label_seg_id = random.randint(0, len(self.parsed_label[label_id]) - 1)
            seg_begin, seg_end = self.parsed_label[label_id][label_seg_id]
            label = np.zeros(1, dtype=float)
            label[0] = self.label[seg_begin, 0].copy()

        data = self.data[seg_begin:seg_end, :].copy()  # 直接通过切片得到的数据是不连续的，不通过copy一下转换成tensor时会报错
        sample = {'data': data, 'label': label, 'mean': self.mean, 'std': self.std}
        if self.transform is not None:
            sample = self.transform(sample)
        return sample

    def parse_label(self):
        # if self.split == 'valid':
        #     self.overlap = 0
        parsed_label = [[] for _ in range(self.class_num)]  # 初始化一个长度为class_num的二维列表
        length = self.window_length
        step = int(length * (1 - self.overlap))
        begin = 0
        end = length
        while end < self.label.shape[0]:
            segment = self.label[begin:end, 0]
            # 说明该段不具备label的重叠，载入数据中
            if len(np.unique(segment)) == 1:
                label_id = self.label[begin, 0]
                parsed_label[label_id].append([begin, end])
            begin += step
            end += step
        return parsed_label

    def min_max_signal(self):
        min_signal = np.min(self.data)
        max_signal = np.max(self.data)
        return min_signal, max_signal

    def min_max_scalar(self):
        self.data = (self.data - self.min_signal) / (self.max_signal - self.min_signal)

    def onehot(self, label_id):
        label = np.zeros(self.class_num, dtype=float)
        label[label_id] = 1
        return label


class ToTensor(object):
    def __call__(self, sample):
        sample['data'], sample['label'] = torch.Tensor(sample['data']), torch.LongTensor(sample['label'])
        sample['data'] = sample['data'].transpose(0, 1)
        sample['label'] = torch.unsqueeze(sample['label'], dim=0)
        return sample

    def __repr__(self):
        return self.__class__.__name__ + '()'


class ToTensor2D(object):
    def __call__(self, sample):
        sample['data'], sample['label'] = torch.Tensor(sample['data']), torch.LongTensor(sample['label'])
        sample['data'] = sample['data'].transpose(0, 1)
        sample['data'] = sample['data'].unsqueeze(0)
        sample['label'] = torch.unsqueeze(sample['label'], dim=0)
        return sample

    def __repr__(self):
        return self.__class__.__name__ + '()'


class Resize(object):
    def __init__(self, size):
        self.size = size

    def __call__(self, sample):
        sample['data'] = F.interpolate(sample['data'], self.size)
        return sample


class Normalize(object):
    def __call__(self, sample):
        data = sample['data']
        mean = sample['mean']
        std = sample['std']
        for i in range(data.shape[1]):
            data[:, i] = (data[:, i] - mean[i]) / std[i]
        sample['data'] = data
        return sample

    def __repr__(self):
        return self.__class__.__name__ + '(mean={0}, std={1})'.format(self.mean, self.std)


class FeatureExtractor(object):
    def __init__(self):
        # 获取类内的所有方法名字
        self.method_list = dir(self)

    def __call__(self, sample):
        feature_list = []
        # 由于继承了nn.module, 里面有一些方法不是计算特征的，要忽略掉
        for method in self.method_list:
            if method[0:2] == 'f_':
                func = getattr(self, method)
                feature_list.append(func(sample['data']))
        feature = np.array(feature_list)
        sample['data'] = feature.flatten()
        return sample


    @staticmethod
    def f_RMS(d):
        return np.sqrt(np.mean(np.square(d), axis=0))

    @staticmethod
    def f_MAV(d):
        return np.mean(np.abs(d), axis=0)

    @staticmethod  # 过零点次数
    def f_ZC(d):
        nZC = np.zeros(d.shape[1])
        th = np.mean(d, axis=0)
        th = np.abs(th)
        for i in range(1, d.shape[0]):
            for j in range(d.shape[1]):
                if d[i - 1, j] < th[j] < d[i, j]:
                    nZC[j] += 1
                elif d[i - 1, j] > th[j] > d[i, j]:
                    nZC[j] += 1
        return nZC / d.shape[0]

    @staticmethod  # slope sign change
    def f_SSC(d):
        nSSC = np.zeros(d.shape[1])
        th = np.mean(d, axis=0)
        th = np.abs(th)
        for i in range(2, d.shape[0]):
            diff1 = d[i] - d[i - 1]
            diff2 = d[i - 1] - d[i - 2]
            for j in range(d.shape[1]):
                if np.abs(diff1[j]) > th[j] and np.abs(diff2[j]) > th[j] and (diff1[j] * diff2[j]) < 0:
                    nSSC[j] += 1
        return nSSC / d.shape[0]

    @staticmethod
    def f_VAR(d):
        feature = np.var(d, axis=0)
        return feature

    # @staticmethod
    # def f_fft(d):
    #     y = fft(d[:, 1])
    #     y_real = y.real
    #     y_imag = y.imag
    #     yf = abs(y)
    #     plt.subplot(411)
    #     plt.plot(y_real)
    #     plt.subplot(412)
    #     plt.plot(y_imag)
    #     plt.subplot(413)
    #     plt.plot(yf)
    #     plt.subplot(414)
    #     plt.plot(d[:, 1])
    #     plt.show()
    #     return yf


if __name__ == '__main__':
    root = r'E:\Datasets\NinaproDataset'
    cutoff_frequency = 45
    sampling_frequency = 100
    wn = 2 * cutoff_frequency / sampling_frequency
    myDataset = NinaProDataset(root=root, split='valid', butterWn=wn, transform=Normalize())
    label_sampled = np.linspace(1, myDataset.class_num, myDataset.class_num)
    length_list = []

    for i in range(len(myDataset.parsed_label)):
        length = len(myDataset.parsed_label[i])
        label_sampled[i] += length
    label_sampled /= np.sum(label_sampled)
    plt.subplot(2, 1, 1)
    plt.bar(x=np.arange(0, myDataset.class_num), height=label_sampled, label='original label number distribution')
    plt.gca().set(xlim=(0, myDataset.class_num), xlabel='label id', ylabel='ratio')
    plt.legend()
    label_sampled = np.linspace(1, myDataset.class_num, myDataset.class_num)
    for batch_id, sample_batch in enumerate(myDataset):
        data, label = sample_batch['data'], sample_batch['label']
        label_sampled[int(label[0])] += 1
        length_list.append(data.shape[0])
    print(label_sampled)
    print(np.mean(np.array(length_list)))
    plt.subplot(2, 1, 2)
    label_sampled /= np.sum(label_sampled)
    plt.bar(x=np.arange(0, myDataset.class_num), height=label_sampled, label='sampleed label number distribution')
    plt.gca().set(xlim=(0, myDataset.class_num), ylim=(0.0, 0.10), xlabel='label id', ylabel='ratio')
    plt.legend()
    plt.tight_layout()
    plt.show()
