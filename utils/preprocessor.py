import torch
import os
import re
import string
import requests
import pickle
import multiprocessing
import pandas as pd

from tqdm import tqdm
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from transformers import BertTokenizer
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler

class Preprocessor(object):
    def __init__(self, method, dataset, batch_size, bert_model):
        super(Preprocessor, self).__init__()
        if not os.path.exists('datasets'):
            os.makedirs('datasets')

        if not os.path.exists(f'datasets/{dataset}_product_tokopedia.csv'):                    
            file = requests.get(f'https://github.com/bintangfjulio/product_categories_classification/releases/download/{dataset}/{dataset}_product_tokopedia.csv', allow_redirects=True)
            open(f'datasets/{dataset}_product_tokopedia.csv', 'wb').write(file.content)

        self.dataset = pd.read_csv(f'datasets/{dataset}_product_tokopedia.csv')
        self.batch_size = batch_size
        self.method = method
        self.stop_words = StopWordRemoverFactory().get_stop_words()
        self.stemmer = StemmerFactory().create_stemmer()
        self.tokenizer = BertTokenizer.from_pretrained(bert_model)
    
    def preprocessor(self, tree, level='all', stage=None):
        if self.method == 'section':
            if not (os.path.exists("datasets/section_train_set.pkl") and os.path.exists("datasets/section_valid_set.pkl") and os.path.exists("datasets/section_test_set.pkl")):
                train_data, test_data = self.split_dataset()
                print("\nPreprocessing Data...")
                for splitted_set in [train_data, test_data]:
                    self.preprocessing_data(dataset=splitted_set, method=self.method, tree=tree, stage=stage)
                print('[ Preprocessing Completed ]\n')

            print("\nLoading Data...")
            with open('datasets/section_train_set.pkl', 'rb') as train_preprocessed:
                train_set = pickle.load(train_preprocessed)
            
            with open('datasets/section_valid_set.pkl', 'rb') as valid_preprocessed:
                valid_set = pickle.load(valid_preprocessed)

            with open('datasets/section_test_set.pkl', 'rb') as test_preprocessed:
                test_set = pickle.load(test_preprocessed)
            print('[ Loading Completed ]\n')

        else:
            if not os.path.exists(f"datasets/{self.method}_level_{str(level)}_train_set.pkl") and not os.path.exists(f"datasets/{self.method}_level_{str(level)}_valid_set.pkl") and not os.path.exists(f"datasets/{self.method}_level_{str(level)}_test_set.pkl"):
                print("\nPreprocessing Data...")
                self.preprocessing_data(dataset=self.dataset, method=self.method, tree=tree, level=level)
                print('[ Preprocessing Completed ]\n')
            
            print("\nLoading Data...")
            train_set = pickle.load(f"datasets/{self.method}_level_{str(level)}_train_set.pkl")
            valid_set = pickle.load(f"datasets/{self.method}_level_{str(level)}_valid_set.pkl")
            test_set = pickle.load(f"datasets/{self.method}_level_{str(level)}_test_set.pkl")
            print('[ Loading Completed ]\n')

        return train_set, valid_set, test_set
        
    def split_dataset(self):
        data = self.dataset
        data = data.sample(frac=1)

        data_len = data.shape[0]
        train_len : int = int(data_len * 0.8)

        train_data = data.iloc[:train_len, :]
        test_data = data.iloc[train_len:, :]

        train_data = pd.DataFrame(train_data)
        test_data = pd.DataFrame(test_data)

        return train_data, test_data
    
    def get_max_length(self, dataset, extra_length=5):
        sentences_token = []
        
        for row in dataset.values.tolist():
            row = str(row[0]).split()
            sentences_token.append(row)

        token_length = [len(token) for token in sentences_token]
        max_length = max(token_length) + extra_length
        
        return max_length
    
    def preprocessing_data(self, dataset, method, tree, level=None, stage=None): 
        level_on_nodes_indexed, idx_on_section, section_on_idx = tree.generate_hierarchy()
        max_length = self.get_max_length(dataset=dataset)
    
        input_ids, target = [], []
        preprocessing_progress = tqdm(dataset.values.tolist())

        section_level_0, section_level_1, section_level_2 = [], [], []

        for row in preprocessing_progress:
            text = self.text_cleaning(str(row[0]))
            token = self.tokenizer(text=text, max_length=max_length, padding="max_length", truncation=True)  

            if method == 'flat':
                last_node = row[-1].split(" > ")[-1].lower()
                flat_target = level_on_nodes_indexed[len(level_on_nodes_indexed) - 1][last_node]
                input_ids.append(token['input_ids'])
                target.append(flat_target)
            
            elif method == 'level':
                node_on_level = row[-1].split(" > ")[level].lower()
                member_on_level = level_on_nodes_indexed[level]
                level_target = member_on_level[node_on_level]
                input_ids.append(token['input_ids'])
                target.append(level_target)

            elif method == 'section':
                if stage == 'fit':
                    nodes = row[-1].lower().split(" > ")
                    
                    section = {}
                    section_idx_list = []

                    for node in nodes:
                        section_idx = section_on_idx[node]
                        nodes_on_section = idx_on_section[section_idx]
                        section_target = nodes_on_section.index(node)
                        section[section_idx] = section_target
                        section_idx_list.append(section_idx)

                    section_level_0.append(section_idx_list[0])
                    section_level_1.append(section_idx_list[1])
                    section_level_2.append(section_idx_list[2])

                    target.append(section)

                elif stage == 'test':
                    last_node = row[-1].split(" > ")[-1].lower()
                    last_section_target = section_on_idx[last_node]
                    target.append(last_section_target)                    
                    
                input_ids.append(token['input_ids'])
                
        if method == 'section':
            section_dataframe = pd.DataFrame({
                'input_ids': input_ids,
                'section': target,
                'level_0': section_level_0,
                'level_1': section_level_1,
                'level_2': section_level_2
            })

            section_dataframe['last_section'] = section_dataframe['section'].apply(lambda row: self.get_last_section_idx(row))
            section_dataframe = section_dataframe.sort_values(by=['last_section'])

            section_keys = list(section_dataframe['last_section'].unique())

            train_ratio = 0.9
            train_dataset, valid_dataset, test_dataset = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            for key in section_keys:
                data_wrapped = section_dataframe.where(section_dataframe['last_section'] == key).dropna(how='all')
                data_wrapped = data_wrapped.sample(frac=1).reset_index(drop=True)

                wrap_size = len(data_wrapped)

                if stage == 'fit':
                    train_size = int(wrap_size * train_ratio)
                    valid_size = wrap_size - train_size
                    
                    train_set = data_wrapped.iloc[:train_size, :]
                    valid_set = data_wrapped.iloc[-valid_size:, :]
                    
                    train_dataset = pd.concat([train_dataset, train_set])
                    valid_dataset = pd.concat([valid_dataset, valid_set])
                
                elif stage == 'test':
                    test_dataset = pd.concat([test_dataset, data_wrapped])

            if stage == 'fit':
                train_dataset = self.hierarchy_section_sorting_dataset(train_dataset)

                with open('datasets/section_train_set.pkl', 'wb') as train_preprocessed :
                    pickle.dump(train_set, train_preprocessed)
                    
                valid_dataset = self.hierarchy_section_sorting_dataset(valid_dataset)
                with open('datasets/section_valid_set.pkl', 'wb') as valid_preprocessed :
                    pickle.dump(valid_set, valid_preprocessed)
            
            elif stage == 'test':
                with open('datasets/section_test_set.pkl', 'wb') as test_preprocessed :
                    pickle.dump(test_dataset, test_preprocessed)
                    
        else:
            train_set, valid_set, test_set = self.dataset_splitting(input_ids, target)

            with open(f"datasets/{self.method}_level_{str(level)}_train_set.pkl", 'wb') as train_preprocessed:
                pickle.dump(train_set, train_preprocessed)

            with open(f"datasets/{self.method}_level_{str(level)}_valid_set.pkl", 'wb') as valid_preprocessed:
                pickle.dump(valid_set, valid_preprocessed)

            with open(f"datasets/{self.method}_level_{str(level)}_test_set.pkl", 'wb') as test_preprocessed:
                pickle.dump(test_set, test_preprocessed)

    def text_cleaning(self, text):
        text = text.lower()
        text = re.sub(r"[^A-Za-z0-9(),!?\'\-`]", " ", text)
        text = re.sub('\n', ' ', text)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'http\S+', '', text)
        text = text.translate(str.maketrans('', '', string.punctuation))
        text = re.sub("'", '', text)
        text = re.sub(r'\d+', '', text)
        text = ' '.join([word for word in text.split() if word not in self.stop_words and len(word) > 1])
        text = self.stemmer.stem(text.strip())

        return text
    
    def hierarchy_section_sorting_dataset(self, dataset):
        keys = [child for child in dataset if child.startswith('level_')]
        dataset = pd.melt(dataset, id_vars=['input_ids', "section"], value_vars=keys, value_name='section_idx').drop("variable", 1)
        dataset["section_idx"] = dataset["section_idx"].astype("int")
        
        dataset = dataset.sort_values(by=["section_idx"])
        existing_section = dataset["section_idx"].unique().tolist()
        
        splitted_dataset = {}

        for idx in existing_section:
            raw_data = dataset.loc[dataset["section_idx"] == idx]
            final_data = self.hierarcy_section_dataloader(raw_data)
            
            splitted_dataset[idx] = final_data
            
        return splitted_dataset
    
    def hierarcy_section_dataloader(self, dataset):
        final_dataset = {
            "input_ids": [],
            "target": []
        }
        
        for _, values in dataset.iterrows():
            final_dataset["input_ids"].append(values["input_ids"])
            final_dataset["target"].append(values["section"][values["section_idx"]])
            
        tensor_set = TensorDataset(torch.tensor(final_dataset["input_ids"]), torch.tensor(final_dataset["target"]))
        return DataLoader(dataset=tensor_set,
                        batch_size=self.batch_size,
                        shuffle=True,
                        num_workers=multiprocessing.cpu_count())

    def dataset_splitting(self, input_ids, target):
        input_ids = torch.tensor(input_ids)
        target = torch.tensor(target)
        
        tensor_dataset = TensorDataset(input_ids, target)

        train_valid_size = round(len(tensor_dataset) * 0.8)
        test_size = len(tensor_dataset) - train_valid_size

        train_valid_set, test_set = torch.utils.data.random_split(tensor_dataset, [train_valid_size, test_size])

        train_size = round(len(train_valid_set) * 0.9)
        valid_size = len(train_valid_set) - train_size

        train_set, valid_set = torch.utils.data.random_split(train_valid_set, [train_size, valid_size])

        return train_set, valid_set, test_set   

    def get_last_section_idx(self, section):
        section_idx = section.keys()

        return list(section_idx)[-1]   

    def flat_dataloader(self, stage, tree):
        flat_train_set, flat_valid_set, flat_test_set = self.preprocessor(tree=tree) 
        
        if stage == 'fit':
            train_dataloader = DataLoader(dataset=flat_train_set,
                                        shuffle=True,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            val_dataloader = DataLoader(dataset=flat_valid_set,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return train_dataloader, val_dataloader

        elif stage == 'test':
            test_dataloader = DataLoader(dataset=flat_test_set,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return test_dataloader  

    def level_dataloader(self, stage, level, tree):
        level_train_set, level_valid_set, level_test_set = self.preprocessor(tree=tree, level=level) 
        
        if stage == 'fit':
            train_dataloader = DataLoader(dataset=level_train_set,
                                        shuffle=True,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            val_dataloader = DataLoader(dataset=level_valid_set,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return train_dataloader, val_dataloader

        elif stage == 'test':
            test_dataloader = DataLoader(dataset=level_test_set,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return test_dataloader
        
    def section_dataloader(self, stage, tree, section):
        section_train_set, section_valid_set, section_test_set = self.preprocessor(tree=tree, stage=stage)

        if stage == 'fit':
            return section_train_set[section], section_valid_set[section]

        elif stage == 'test':
            test_dataloader = DataLoader(dataset=section_test_set,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return test_dataloader
