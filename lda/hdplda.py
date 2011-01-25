#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Hierarchical Dirichlet Process - Latent Dirichlet Allocation
# (c)2010 Nakatani Shuyo / Cybozu Labs Inc.
# (refer to "Hierarchical Dirichlet Processes"(Teh et.al, 2005))

import sys, re, math
from optparse import OptionParser
from scipy.special import gammaln
import scipy.stats
import vocabulary
import numpy

class HDPLDA:
    def __init__(self, alpha, gamma, base):
        self.alpha = alpha
        self.base = base
        self.gamma = gamma

    def set_corpus(self, corpus, stopwords):
        self.x_ji = [] # vocabulary for each document and term
        self.t_ji = [] # table for each document and term
        self.k_jt = [] # topic for each document and table
        self.n_jt = [] # number of terms for each document and table

        self.tables = [] # available id of tables for each document
        self.topics = [0] # available id of topics
        self.n_terms = 0

        voca = vocabulary.Vocabulary(stopwords)
        n_v = dict()
        for doc in corpus:
            x_i = voca.doc_to_ids(doc)
            self.x_ji.append(x_i)
            for v in x_i:
                if v in n_v:
                    n_v[v] += 1
                else:
                    n_v[v] = 1

            self.k_jt.append([0])
            self.n_jt.append([len(x_i)])
            self.n_terms += len(x_i)
            self.t_ji.append([0] * len(x_i))
            self.tables.append([0])

        self.n_kv = [n_v] # number of terms for each topic and vocabulary
        self.n_k = [self.n_terms] # number of terms for each topic
        self.m_k = [len(corpus)] # number of tables for each topic
        self.n_tables = len(corpus)

        self.V = voca.size()
        return voca

    def dump(self, disp_x=False):
        if disp_x: print "x_ji:", self.x_ji
        print "t_ji:", self.t_ji
        print "k_jt:", self.k_jt
        print "n_kv:", self.n_kv
        print "n_jt:", self.n_jt
        print "n_k:", self.n_k
        print "m_k:", self.m_k
        print "tables:", self.tables
        print "topics:", self.topics


    # n_??/m_? を用いて f_k を高速に計算
    def f_k_x_ji_fast(self, k, j, i):
        n_kv = self.n_kv[k].get(self.x_ji[j][i], 0)
        return (n_kv + self.base) / (self.n_k[k] + self.base * self.V)

    def f_k_new_x_ji_fast(self):
        return 1.0 / self.V

    def log_f_k_x_jt_fast(self, k, j, t):
        return self.log_f_k_new_x_jt_fast(j, t, self.n_kv[k].copy(), self.n_k[k])

    # 浮動小数の範囲を超えて非常に小さい値になることがあるので、対数を返す
    def log_f_k_new_x_jt_fast(self, j, target_t, n_v = False, n = 0):
        if not n_v: n_v = dict()
        Vbase = self.base * self.V
        p = 0.0
        for v, t in zip(self.x_ji[j], self.t_ji[j]):
            if t != target_t: continue
            p += math.log(n_v.setdefault(v, 0) + self.base) - math.log(n + Vbase)
            n_v[v] += 1
            n += 1
        return p


    # p(x_{guard}|X_k^{-guard}) の分子 p(x_{guard}, X_k^{-guard}) と分母 p(X_k^{-guard}) を
    # n_??/m_?? を用いずに計算( x_ji, t_ji, k_jt のみ参照 )
    # ただし固定の正規化項 Γ(Σβ)/ΠΓ(β) は含まない
    # 浮動小数の範囲を超えて非常に小さい値になることがあるので、対数を返す
    def log_p_X_k(self, target_k, gaurd, is_denom=False):
        n_v = [0] * self.V
        for j in range(len(self.x_ji)):
            for i in range(len(self.x_ji[j])):
                t = self.t_ji[j][i]
                k = self.k_jt[j][t]
                g = gaurd(j, i, t)
                if (k == target_k and not g) or (is_denom and g):
                    n_v[self.x_ji[j][i]] += 1
        return sum([gammaln(self.base + n) for n in n_v]) - gammaln(self.base * self.V + sum(n_v))

    # p(x_ji|X_k^{-ji})
    def f_k_x_ji(self, k, target_j, target_i):
        guard = lambda j,i,t:j == target_j and i == target_i
        return math.exp(self.log_p_X_k(k, guard, is_denom=True) - self.log_p_X_k(k, guard))

    # p(x_jt|X_k^{-jt})
    # 浮動小数の範囲を超えて非常に小さい値になることがあるので、対数を返す
    def log_f_k_x_jt(self, k, target_j, target_t):
        guard = lambda j,i,t:j == target_j and t == target_t
        return self.log_p_X_k(k, guard, is_denom=True) - self.log_p_X_k(k, guard)


    # 分布から k をサンプリング
    # 新しいトピックの場合、パラメータの領域を確保
    def sampling_topic(self, p_k):
        Z = sum(p_k)
        p_k = [p / Z for p in p_k]
        dist = scipy.stats.rv_discrete(values=(self.topics + [-1], p_k))
        k_new = dist.rvs()

        # 新しいトピック
        if k_new < 0:
            # 空きトピックIDを取得
            for k_new in range(len(self.m_k)):
                if k_new not in self.topics: break
            else:
                # 新しいテーブルID
                k_new = len(self.n_k)
                self.n_k.append(0)
                self.m_k.append(0)
                self.n_kv.append(dict())
            self.topics.append(k_new)
        return k_new

    # 客 x_ji を新しいテーブルに案内
    # テーブルのトピック(料理)もサンプリング
    def new_table(self, j, i):
        # 空きテーブルIDを取得
        for t_new in range(len(self.n_jt[j])):
            if t_new not in self.tables[j]: break
        else:
            # 新しいテーブルID
            t_new = len(self.n_jt[j])
            self.n_jt[j].append(0)
            self.k_jt[j].append(0)
        self.tables[j].append(t_new)
        self.n_tables += 1

        # sampling of k (新しいテーブルの料理(トピック))
        p_k = [self.m_k[k] * self.f_k_x_ji_fast(k, j, i) for k in self.topics]
        p_k.append(self.gamma * self.f_k_new_x_ji_fast())
        k_new = self.sampling_topic(p_k)

        self.k_jt[j][t_new] = k_new
        self.m_k[k_new] += 1

        return t_new


    # 事後分布から t をサンプリング
    def sampling_t(self, j, i):
        #assert [sum(x.values()) for x in self.n_kv] == self.n_k

        v = self.x_ji[j][i]
        t_old = self.t_ji[j][i]
        k_old = self.k_jt[j][t_old]

        self.n_kv[k_old][v] -= 1
        self.n_k[k_old] -= 1
        self.n_jt[j][t_old] -= 1

        if self.n_jt[j][t_old]==0:
            # 客がいなくなったテーブル
            self.tables[j].remove(t_old)
            self.m_k[k_old] -= 1
            self.n_tables -= 1

        if self.m_k[k_old] == 0:
            #assert self.n_k[k_old] == 0
            # 客がいなくなった料理(トピック)
            self.topics.remove(k_old)
            self.n_kv[k_old] = dict()

        # sampling of t ( p(t_ji=t) を求める )
        p_t = [self.n_jt[j][t] * self.f_k_x_ji_fast(self.k_jt[j][t], j, i) for t in self.tables[j]]

        f = self.f_k_new_x_ji_fast()
        #assert abs(self.f_k_x_ji(-1, j, i) - f) < 1e-10, self.dump()
        p_x_ji = self.gamma * f
        for k in self.topics:
            f = self.f_k_x_ji_fast(k, j, i)
            #assert abs(self.f_k_x_ji(k, j, i) - f) < 1e-10, self.dump()
            p_x_ji += self.m_k[k] * f
        p_t.append(p_x_ji * self.alpha / (self.n_tables + self.gamma))

        Z_p_t = sum(p_t)
        p_t = [p / Z_p_t for p in p_t]
        dist = scipy.stats.rv_discrete(values=(self.tables[j] + [-1], p_t))
        t_new = dist.rvs()
        if t_new < 0: t_new = self.new_table(j, i)

        # パラメータの更新
        self.t_ji[j][i] = t_new
        self.n_jt[j][t_new] += 1

        k_new = self.k_jt[j][t_new]
        self.n_k[k_new] += 1
        self.n_kv[k_new][v] = self.n_kv[k_new].get(v, 0) + 1
        #assert [sum(x.values()) for x in self.n_kv] == self.n_k

    # 事後分布から k をサンプリング
    def sampling_k(self, j, t):
        k_old = self.k_jt[j][t]
        self.m_k[k_old] -= 1
        self.n_k[k_old] -= self.n_jt[j][t]
        if self.m_k[k_old] > 0:
            for v, t1 in zip(self.x_ji[j], self.t_ji[j]):
                if t1 != t: continue
                #assert self.n_kv[k_old][v] > 0
                self.n_kv[k_old][v] -= 1
        else:
            #assert self.n_k[k_old] == 0
            self.n_kv[k_old] = dict()
            self.topics.remove(k_old)

        # sampling of k
        log_p_k = []
        for k in self.topics:
            f = self.log_f_k_x_jt_fast(k, j, t)
            #assert abs(f - self.log_f_k_x_jt(k, j, t)) < 1e-10, self.dump()
            log_p_k.append(f + math.log(self.m_k[k]))
        f = self.log_f_k_new_x_jt_fast(j, t)
        #assert abs(f - self.log_f_k_x_jt(-1, j, t)) < 1e-10, self.dump()
        p = math.log(self.gamma) + f
        log_p_k.append(p)

        # 確率が小さくなりすぎるので log で保持。最大値を引いてからexp&正規化
        max_log_p_k = max(log_p_k)
        p_k = [math.exp(p - max_log_p_k) for p in log_p_k]
        k_new = self.sampling_topic(p_k)

        # パラメータの更新
        self.k_jt[j][t] = k_new
        self.m_k[k_new] += 1
        self.n_k[k_new] += self.n_jt[j][t]
        for v, t1 in zip(self.x_ji[j], self.t_ji[j]):
            if t1 != t: continue
            self.n_kv[k_new][v] = self.n_kv[k_new].get(v, 0) + 1

    def inference(self):
        for j in range(len(self.x_ji)):
            for i in range(len(self.x_ji[j])):
                self.sampling_t(j, i)
        for j in range(len(self.x_ji)):
            for t in self.tables[j]:
                self.sampling_k(j, t)

    def worddist(self):
        def freq2prob(freq, n_k, base, V):
            prob = numpy.zeros(V)
            for v in freq:
                prob[v] = (freq[v] + base) / (n_k + V * base)
            return prob
        return [freq2prob(self.n_kv[k], self.n_k[k], self.base, self.V) for k in self.topics]

    def predictive(self, doc):
        pass

def main():
    parser = OptionParser()
    parser.add_option("-f", dest="filename", help="corpus filename")
    parser.add_option("-r", dest="reuters", help="corpus range of Reuters' files(start:end)")
    parser.add_option("--alpha", dest="alpha", type="float", help="parameter alpha", default=scipy.stats.gamma.rvs(1,scale=1))
    parser.add_option("--gamma", dest="gamma", type="float", help="parameter gamma", default=scipy.stats.gamma.rvs(1,scale=1))
    parser.add_option("--base", dest="base", type="float", help="parameter of base measure H", default=0.5)
    parser.add_option("-i", dest="iteration", type="int", help="iteration count", default=10)
    parser.add_option("-s", dest="stopwords", type="int", help="except stop words", default=1)
    (options, args) = parser.parse_args()
    if not (options.filename or options.reuters): parser.error("need corpus filename(-f) or Reuters range(-r)")

    if options.filename:
        corpus = vocabulary.load_corpus(options.filename)
    else:
        corpus = vocabulary.load_reuters(options.reuters)
        if not corpus: parser.error("Reuters range(-r) forms 'start:end'")

    hdplda = HDPLDA( options.alpha, options.gamma, options.base )
    voca = hdplda.set_corpus(corpus, options.stopwords)
    #hdplda.dump(True)
    print "corpus=%d words=%d alpha=%f gamma=%f base=%f" % (len(corpus), len(voca.vocas), options.alpha, options.gamma, options.base)

    for i in range(options.iteration):
        sys.stderr.write("-%d " % (i + 1))
        hdplda.inference()
        #hdplda.dump()

    phi = hdplda.worddist()
    #for v, term in enumerate(voca):
    #    print ','.join([term]+[str(x) for x in phi[:,v]])
    for k in range(len(phi)):
        print "\n-- topic: %d" % k
        for w in numpy.argsort(-phi[k])[:20]:
            print "%s: %f" % (voca[w], phi[k][w])

if __name__ == "__main__":
    main()
