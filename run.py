import statistics

import deap.gp
import numpy
import numpy as np
from time import process_time
import sklearn.metrics

from src.utils import fetch_dataset
from src.models.classifiers import mlp

import operator

from deap import algorithms
from deap import gp
from deap import creator, base, tools

X_train = []
X_test = []
y_train = []
y_test = []
opaque_model_prediction_test = []
opaque_model_prediction_train = []
toolbox = None
mlp_time = 0
time_start = 0
time_end = 0
mlp_classifier = None
dataset_name = ''
logbook = tools.Logbook()


def protectedDiv(left, right):
    try:
        if right == 0:
            right = 1
        return left / right
    except ZeroDivisionError:
        return 1


def setUpGP(n_parameters: int, evaluate_function):
    global toolbox

    pset = gp.PrimitiveSet("MAIN", n_parameters)
    pset.addPrimitive(operator.add, 2)
    pset.addPrimitive(operator.sub, 2)
    pset.addPrimitive(operator.mul, 2)
    pset.addPrimitive(protectedDiv, 2)
    pset.renameArguments(ARG0='x', ARG1='y', ARG2='z', ARG3='t')

    creator.create("FitnessMulti", base.Fitness, weights=(1.0, -1.0, -1.0))
    creator.create("Individual", gp.PrimitiveTree, fitness=creator.FitnessMulti)

    toolbox = base.Toolbox()
    toolbox.register("expr", gp.genHalfAndHalf, pset=pset, min_=1, max_=2)
    toolbox.register("individual", tools.initIterate, creator.Individual, toolbox.expr)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("compile", gp.compile, pset=pset)

    toolbox.register("evaluate", evaluate_function)
    toolbox.register("select", tools.selNSGA2)
    toolbox.register("mate", gp.cxOnePoint)
    toolbox.register("expr_mut", gp.genFull, min_=0, max_=2)
    toolbox.register("mutate", gp.mutUniform, expr=toolbox.expr_mut, pset=pset)

    toolbox.decorate("mate", gp.staticLimit(key=operator.attrgetter("height"), max_value=50))
    toolbox.decorate("mutate", gp.staticLimit(key=operator.attrgetter("height"), max_value=50))

    return toolbox


def fitness_function(individual):
    # Evaluate fitness of an individual within a generation.
    import math
    func = toolbox.compile(expr=individual)
    split_points = 0
    countDivision = 0
    countMult = 0
    countPrimitive = 0
    countTerminals = 0
    complexity = 0
    for i in individual.copy():
        split_points += i.arity if type(i) == deap.gp.Primitive else 0
        countPrimitive += 1 if type(i) == deap.gp.Primitive else 0
        complexity += getComplexityFactor(i.name) if type(i) == deap.gp.Primitive else 0
        countTerminals += 1 if type(i) == deap.gp.Terminal else 0
        countDivision += 1 if type(i) == deap.gp.Primitive and i.name == 'protectedDiv' else 0
        countMult += 1 if type(i) == deap.gp.Primitive and i.name == 'mul' else 0
    y_pred = []

    for x in enumerate(X_train):
        function_result = int(func(*x[1]) > 0.5)
        y_pred.append(function_result)

    # avgTreeLength = individual.__len__() / split_points if split_points != 0 else 0
    ari = 1 / (1 + math.exp(-(countTerminals * countPrimitive)))
    complextTerminals = 1 / (1 + math.exp(-complexity))

    return sklearn.metrics.f1_score(opaque_model_prediction_train, y_pred), ari, complextTerminals


def getComplexityFactor(primitive):
    if primitive == 'add':
        return 1
    elif primitive == 'sub':
        return 1
    elif primitive == 'mul':
        return 5
    elif primitive == 'protectedDiv':
        return 10


# Calculates score for test dataset in comparison with black-box
def calculateScore(individuals, pareto):
    global opaque_model_prediction_test
    hallOfFame = []
    f1_score_list = []
    f1_score_sum = 0
    hof_height_sum = []
    hof_node_sum = []

    for i in individuals:
        func = toolbox.compile(expr=i)
        y_gp = []
        for x in enumerate(X_test):
            function_result = int(func(*list(x[1])) > 0.5)
            y_gp.append(function_result)

        gp_f1score = sklearn.metrics.f1_score(opaque_model_prediction_test, np.array(y_gp))
        gp_accuracy_score = sklearn.metrics.accuracy_score(opaque_model_prediction_test, np.array(y_gp))
        hallOfFame.append((gp_f1score, i, gp_accuracy_score))
        f1_score_sum += gp_f1score

    for individual in hallOfFame:
        hof_height_sum.append(individual[1].height)
        hof_node_sum.append(individual[1].__len__())

    hallOfFame.sort(key=lambda x: x[0], reverse=True)

    mlp_fscore = sklearn.metrics.f1_score(y_test, opaque_model_prediction_test)
    mlp_accuracy = sklearn.metrics.accuracy_score(y_test, opaque_model_prediction_test)

    return hallOfFame[0][0], hallOfFame[0][1], mlp_fscore, hallOfFame[0][2], \
           mlp_accuracy, \
           sum(hof_height_sum) / len(hallOfFame), sum(hof_node_sum) / len(hallOfFame), \
           pareto,


def executeGeneticProgramming():
    global X_train
    global logbook
    pareto = tools.ParetoFront()
    generation = 40

    toolbox = setUpGP(len(X_train[0]), fitness_function)

    pop = toolbox.population(n=300)
    hof = tools.HallOfFame(10)
    fscore_stats = tools.Statistics(lambda ind: ind.fitness.values[0])
    # avgTree_stats = tools.Statistics(lambda ind: ind.fitness.values[1])
    ari_stats = tools.Statistics(lambda ind: ind.fitness.values[1])
    complexTerminals_stats = tools.Statistics(lambda ind: ind.fitness.values[2])
    mstats = tools.MultiStatistics(fscore_stats=fscore_stats, ari_stats=ari_stats,
                                   complexTerminals_stats=complexTerminals_stats)
    mstats.register("avg", numpy.mean)
    mstats.register("std", numpy.std)
    mstats.register("min", numpy.min)
    mstats.register("max", numpy.max)

    logbook.header = ["gen", "evals"] + mstats.fields

    algorithms.eaSimple(pop, toolbox, 0.5, 0.1, generation, mstats, halloffame=hof, verbose=True)
    pareto.update(pop)

    logbook.record(gen=generation, evals=len(pop), **mstats.compile(pop))

    return calculateScore(hof, pareto),


def generateReport(n_experiments, best_pareto=None):
    import pandas as pd
    global logbook
    global dataset_name

    gp_fscore_sum = []
    gp_accuracy_sum = []
    gp_height_sum = []
    gp_node_sum = []

    mlp_fscore_sum = []
    mlp_accuracy_sum = []

    best_gp_fscore = 0
    best_gp_function = None
    gp_sum_time = 0
    dt_sum_time = 0
    best_pareto
    accumulatedPareto = []
    for i in range(0, n_experiments):
        time_start = process_time()
        gp_fscore, gp_function, mlp_fscore, accuracy_score, mlp_accuracy, gp_height, gp_node, pareto_ = \
            executeGeneticProgramming()[0]
        time_end = process_time()
        gp_sum_time += time_end - time_start

        accumulatedPareto.append(pareto_)

        mlp_fscore_sum.append(mlp_fscore)
        mlp_accuracy_sum.append(mlp_accuracy)

        gp_fscore_sum.append(gp_fscore)
        gp_accuracy_sum.append(accuracy_score)
        gp_height_sum.append(gp_height)
        gp_node_sum.append(gp_node)

        if gp_fscore > best_gp_fscore:
            best_gp_fscore = gp_fscore
            best_pareto = pareto_

    fit_max = logbook.chapters["fscore_stats"].select("max")

    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots()
    ax1.plot(range(0, 30), fit_max, "b-", label="Maximum Fitness")
    ax1.set_xlabel("Experiments")
    ax1.set_ylabel("F1-Score", color="b")
    for tl in ax1.get_yticklabels():
        tl.set_color("b")
    plt.savefig("pareto_results/" + dataset_name + "/fscore.png")

    results_log = [{
        'mlp_fscore': sum(mlp_fscore_sum) / n_experiments,
        'mlp_fscore_std': statistics.pstdev(mlp_fscore_sum),
        'gp_fscore_avg': sum(gp_fscore_sum) / n_experiments,
        'gp_fscore_std': statistics.pstdev(gp_fscore_sum),
        'total_pareto': best_pareto.items.__len__()
    }]
    df_logbook = pd.DataFrame(results_log)
    df_logbook.to_csv("pareto_results/" + dataset_name + "/results.csv")
    generateParetoCharts(accumulatedPareto)
    # generateTree(best_pareto)


def generateParetoCharts(paretos):
    import matplotlib.pyplot as plt
    global dataset_name

    fscoreData = []
    ariData = []
    complexTermData = []
    for p in paretos:
        for i in p.items:
            fscore, ari, complexTerminals = fitness_function(i)
            fscoreData.append(fscore)
            ariData.append(ari)
            complexTermData.append(complexTerminals)

    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    xline = complexTermData
    zline = ariData
    yline = fscoreData
    ax.scatter(xline, yline, zline, c='red', label=['complex terminals', 'fscore', 'ari'], lineWidth=0.5)
    ax.set_xlabel('complex terminals', fontweight='bold')
    ax.set_ylabel('fscore', fontweight='bold')
    ax.set_zlabel('ari', fontweight='bold')

    plt.savefig("pareto_results/" + dataset_name + "/breast_pareto.png")

    # plt.show()

    fig1, ax1 = plt.subplots()
    breast_gpx_fscore = [0.761904761904762, 0.37340153452685426, 0.822463768115942, 0.7686116700201208,
                         0.7482219061166429, 0.35543766578249336, 0.8293650793650793, 0.7514677103718199,
                         0.7329376854599406, 0.3517060367454068, 0.8556521739130434, 0.7237762237762237,
                         0.37340153452685426, 0.8615384615384616, 0.8541300527240774, 0.7410714285714286,
                         0.2872340425531915, 0.8871224165341811, 0.8699029126213592, 0.8028933092224231,
                         0.761904761904762, 0.37340153452685426, 0.8650646950092421, 0.761904761904762,
                         0.37340153452685426, 0.8463073852295409, 0.8066528066528067, 0.761904761904762,
                         0.761904761904762, 0.37340153452685426]
    breast_gpx_split = [0.8807970779778823, 0.8807970779778823, 0.5, 0.9975273768433653, 0.9820137900379085,
                        0.8807970779778823, 0.5, 0.9975273768433653, 0.9820137900379085, 0.8807970779778823, 0.5,
                        0.9820137900379085, 0.8807970779778823, 0.5, 0.9999991684719722, 0.9820137900379085,
                        0.8807970779778823, 0.5, 0.9996646498695336, 0.9975273768433653, 0.9820137900379085,
                        0.8807970779778823, 0.5, 0.9975273768433653, 0.8807970779778823, 0.5, 0.9996646498695336,
                        0.9820137900379085, 0.8807970779778823, 0.5]
    breast_const_fscore = [0.8731884057971014, 0.8163265306122449, 0.7702702702702703, 0.33591731266149866,
                           0.8490230905861458, 0.7407407407407406, 0.33591731266149866, 0.8432432432432433,
                           0.8220064724919094, 0.807843137254902, 0.7992633517495397, 0.7702702702702703,
                           0.35989717223650386, 0.8553259141494435, 0.7432835820895523, 0.33591731266149866,
                           0.8597785977859779, 0.8447937131630648, 0.7846153846153846, 0.7586206896551724,
                           0.35989717223650386, 0.8492201039861351, 0.7687776141384389, 0.33591731266149866,
                           0.8697247706422019, 0.8496503496503497, 0.7702702702702703, 0.7702702702702703,
                           0.7702702702702703, 0.35989717223650386]
    breast_const_simplicity = [0.9985950204031521, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
                               0.9895009751316033, 0.9259109423490272, 0.6236575784782976, 0.9999752619396515,
                               0.9998134705161004, 0.9985950204031521, 0.9895009751316033, 0.9259109423490272,
                               0.6236575784782976, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
                               0.999999992351647, 0.9999752619396515, 0.9985950204031521, 0.9259109423490272,
                               0.6236575784782976, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
                               0.9985950204031521, 0.9895009751316033, 0.9259109423490272, 0.9259109423490272,
                               0.9259109423490272, 0.6236575784782976]

    line1, = ax1.plot(breast_gpx_split, breast_gpx_fscore, 'ro', label='gpx')
    line2, = ax1.plot(breast_const_simplicity, breast_const_fscore, 'gs', label='constrained')
    line3, = ax1.plot(ariData, fscoreData, 'k+', label='ari-remo-gp')
    ax1.legend(handles=[line1, line2, line3])
    ax1.set(xlabel='objectives', ylabel='fscore',
            title='')
    ax1.grid()

    fig1.savefig("pareto_results/" + dataset_name + "/breast_compare_ari.png")

    fig2, ax2 = plt.subplots()
    line1, = ax2.plot(breast_gpx_split, breast_gpx_fscore, 'ro', label='gpx')
    line2, = ax2.plot(breast_const_simplicity, breast_const_fscore, 'gs', label='constrained')
    line3, = ax2.plot(complexTermData, fscoreData, 'k+', label='complex-remo-gp')
    ax2.legend(handles=[line1, line2, line3])
    ax2.set(xlabel='objectives', ylabel='fscore',
            title='')
    ax2.grid()

    fig2.savefig("pareto_results/" + dataset_name + "/breast_compare_complex.png")

    fig3, ax3 = plt.subplots()
    line1, = ax3.plot(breast_gpx_split, breast_gpx_fscore, 'ro', label='gpx')
    line2, = ax3.plot(breast_const_simplicity, breast_const_fscore, 'gs', label='constrained')
    line3, = ax3.plot(ariData, fscoreData, 'k+', label='ari-remo-gp')
    line4, = ax3.plot(complexTermData, fscoreData, 'y+', label='complex-remo-gp')
    ax3.legend(handles=[line1, line2, line3, line4])
    ax3.set(xlabel='objectives', ylabel='fscore',
            title='')
    ax3.grid()

    fig3.savefig("pareto_results/" + dataset_name + "/breast_compare_ari_complex.png")

    # ax1.set(xlabel='automated readability index', ylabel='fscore',
    #         title='')
    # ax1.grid()
    #
    # fig1.savefig("pareto_results/" + dataset_name + "/breast_ari.png")
    # # plt.show()
    #
    # fig2, ax2 = plt.subplots()
    # ax2.plot(complexTermData, fscoreData, 'ro')
    #
    # ax2.set(xlabel='complex terminals', ylabel='fscore',
    #         title='')
    # ax2.grid()
    #
    # fig2.savefig("pareto_results/" + dataset_name + "/complexTerminals.png")
    # # plt.show()


def generateTree(best_pareto):
    import pygraphviz as pgv
    global dataset_name
    # nodes, edges, labels = gp.graph(best_gp_function)
    #
    # g = pgv.AGraph()
    # g.add_nodes_from(nodes)
    # g.add_edges_from(edges)
    # g.layout(prog="dot")
    #
    # for i in nodes:
    #     n = g.get_node(i)
    #     n.attr["label"] = labels[i]
    #
    # g.draw("hof_results/tree_hall_of_fame.pdf")

    pareto_draw = sorted(best_pareto.items, key=lambda i: i.__len__())

    for i_pareto in pareto_draw:
        nodes, edges, labels = gp.graph(i_pareto)

        g = pgv.AGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from(edges)
        g.layout(prog="dot")

        for i in nodes:
            n = g.get_node(i)
            n.attr["label"] = labels[i]

        g.draw("pareto_results/" + dataset_name + "/tree_pareto_" + str(pareto_draw.index(i_pareto)) + ".pdf")


def main(dataset):
    # Get global scope variables
    global X_train, X_test, y_train, y_test, toolbox, opaque_model_prediction_test, opaque_model_prediction_train, mlp_time

    if dataset == 'ionosphere':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_ionosphere()
    elif dataset == 'breast_cancer':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_breast_cancer()
    elif dataset == 'digits1_7':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_digits(1, 7)
    elif dataset == 'digits3_9':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_digits(3, 9)
    elif dataset == 'wine':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_wine()
    elif dataset == 'banknotes':
        # Fetch dataset and set train/test variables
        X_train, X_test, y_train, y_test = fetch_dataset.fetch_banknotes()

    # Execute blackbox algorithm
    opaque_model_prediction_test, opaque_model_prediction_train, classifier, mlp_time = mlp.createInstance(X_train, X_test,
                                                                                                           y_train, y_test)

    generateReport(n_experiments=30)


if __name__ == '__main__':
    time_start = process_time()

    # dataset_name = 'wine'
    # main(dataset_name)

    # dataset_name = 'ionosphere'
    # main(dataset_name)

    # dataset_name = 'breast_cancer'
    # main(dataset_name)

    dataset_name = 'digits1_7'
    main(dataset_name)

    # dataset_name = 'digits3_9'
    # main(dataset_name)

    # dataset_name = 'banknotes'
    # main(dataset_name)
    # import matplotlib.pyplot as plt
    # fig1, ax1 = plt.subplots()
    # breast_gpx_fscore = [0.761904761904762, 0.37340153452685426, 0.822463768115942, 0.7686116700201208,
    #                      0.7482219061166429, 0.35543766578249336, 0.8293650793650793, 0.7514677103718199,
    #                      0.7329376854599406, 0.3517060367454068, 0.8556521739130434, 0.7237762237762237,
    #                      0.37340153452685426, 0.8615384615384616, 0.8541300527240774, 0.7410714285714286,
    #                      0.2872340425531915, 0.8871224165341811, 0.8699029126213592, 0.8028933092224231,
    #                      0.761904761904762, 0.37340153452685426, 0.8650646950092421, 0.761904761904762,
    #                      0.37340153452685426, 0.8463073852295409, 0.8066528066528067, 0.761904761904762,
    #                      0.761904761904762, 0.37340153452685426]
    # breast_gpx_split = [0.8807970779778823, 0.8807970779778823, 0.5, 0.9975273768433653, 0.9820137900379085,
    #                     0.8807970779778823, 0.5, 0.9975273768433653, 0.9820137900379085, 0.8807970779778823, 0.5,
    #                     0.9820137900379085, 0.8807970779778823, 0.5, 0.9999991684719722, 0.9820137900379085,
    #                     0.8807970779778823, 0.5, 0.9996646498695336, 0.9975273768433653, 0.9820137900379085,
    #                     0.8807970779778823, 0.5, 0.9975273768433653, 0.8807970779778823, 0.5, 0.9996646498695336,
    #                     0.9820137900379085, 0.8807970779778823, 0.5]
    # breast_const_fscore = [0.8731884057971014, 0.8163265306122449, 0.7702702702702703, 0.33591731266149866,
    #                        0.8490230905861458, 0.7407407407407406, 0.33591731266149866, 0.8432432432432433,
    #                        0.8220064724919094, 0.807843137254902, 0.7992633517495397, 0.7702702702702703,
    #                        0.35989717223650386, 0.8553259141494435, 0.7432835820895523, 0.33591731266149866,
    #                        0.8597785977859779, 0.8447937131630648, 0.7846153846153846, 0.7586206896551724,
    #                        0.35989717223650386, 0.8492201039861351, 0.7687776141384389, 0.33591731266149866,
    #                        0.8697247706422019, 0.8496503496503497, 0.7702702702702703, 0.7702702702702703,
    #                        0.7702702702702703, 0.35989717223650386]
    # breast_const_simplicity = [0.9985950204031521, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
    #                            0.9895009751316033, 0.9259109423490272, 0.6236575784782976, 0.9999752619396515,
    #                            0.9998134705161004, 0.9985950204031521, 0.9895009751316033, 0.9259109423490272,
    #                            0.6236575784782976, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
    #                            0.999999992351647, 0.9999752619396515, 0.9985950204031521, 0.9259109423490272,
    #                            0.6236575784782976, 0.9895009751316033, 0.9259109423490272, 0.6236575784782976,
    #                            0.9985950204031521, 0.9895009751316033, 0.9259109423490272, 0.9259109423490272,
    #                            0.9259109423490272, 0.6236575784782976]
    #
    # line1, = ax1.plot(breast_gpx_split, breast_gpx_fscore, 'ro', label='gpx')
    # line2, = ax1.plot(breast_const_simplicity,breast_const_fscore , 'gs', label='constrained')
    # ax1.legend(handles=[line1, line2])
    # # ax1.plot(fscoreData, ariData, 'k+')
    # ax1.set(xlabel='comparison', ylabel='fscore',
    #         title='')
    # ax1.grid()
    #
    # fig1.savefig("pareto_results/" + dataset_name + "/breast_compare_ari.png")

    time_end = process_time()

    print(time_end - time_start)
