#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Simulate the Interference Alignment algorithm described in the paper
??????

"""
# xxxxxxxxxx Add the parent folder to the python path. xxxxxxxxxxxxxxxxxxxx
import sys
import os
parent_dir = os.path.split(os.path.abspath(os.path.dirname(__file__)))[0]
sys.path.append(parent_dir)
# xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

from util.simulations import *
from comm import modulators, channels
from util.conversion import dB2Linear
from util import misc
from ia import ia
import numpy as np
from pprint import pprint


# xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
class MaxSINRSimulationRunner(SimulationRunner):
    """Implements a simulation runner for a transmission with the
    MaxSINR Minimizations Interference Alignment Algorithm.
    """
    def __init__(self, config_filename):
        SimulationRunner.__init__(self)

        # xxxxxxxxxx Read Parameters from file xxxxxxxxxxxxxxxxxxxxxxxxxxxx
        spec = """[Scenario]
        SNR=real_numpy_array(min=0, max=100, default=0:5:31)
        M=integer(min=4, max=512, default=4)
        modulator=option('QPSK', 'PSK', 'QAM', 'BPSK', default="PSK")
        NSymbs=integer(min=10, max=1000000, default=200)
        K=integer(min=2,default=3)
        Nr=integer(min=2,default=2)
        Nt=integer(min=2,default=2)
        Ns=integer(min=1,default=1)
        [IA Algorithm]
        max_iterations=integer(min=1, default=120)
        [General]
        rep_max=integer(min=1, default=2000)
        max_bit_errors=integer(min=1, default=3000)
        unpacked_parameters=string_list(default=list('SNR'))
        """.split("\n")

        self.params = SimulationParameters.load_from_config_file(
            config_filename,
            spec,
            save_parsed_file=True)

        # Set the max_bit_errors and rep_max attributes
        self.max_bit_errors = self.params['max_bit_errors']
        self.rep_max = self.params['rep_max']

        # Create the modulator object
        M = self.params['M']
        modulator_options = {'PSK': modulators.PSK,
                             'QPSK': modulators.QPSK,
                             'QAM': modulators.QAM,
                             'BPSK': modulators.BPSK}
        self.modulator = modulator_options[self.params['modulator']](M)

        # Create the channel object
        self.multiUserChannel = channels.MultiUserChannelMatrix()

        # Create the IA Solver object. Noise_power will be changed later
        # depending on the SNR value
        self.ia_solver = ia.MaxSinrIASolver(self.multiUserChannel, noise_power=1)
        # Iterations of the MinLeakageMinIASolver algorithm.
        self.ia_solver.max_iterations = self.params['max_iterations']
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxxxxxxx Set the progressbar message xxxxxxxxxxxxxxxxxxxxxxxxxx
        self.progressbar_message = "Max SINR ({0} mod.) - SNR: {{SNR}}".format(self.modulator.name)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    def _run_simulation(self, current_parameters):
        # xxxxx Input parameters (set in the constructor) xxxxxxxxxxxxxxxxx
        M = self.modulator.M
        NSymbs = current_parameters["NSymbs"]
        K = current_parameters["K"]
        Nr = np.ones(K, dtype=int) * current_parameters["Nr"]
        Nt = np.ones(K, dtype=int) * current_parameters["Nt"]
        Ns = np.ones(K, dtype=int) * current_parameters["Ns"]
        SNR = current_parameters["SNR"]
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Input Data xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        # inputData has the data of all users (vertically stacked)
        inputData = np.random.randint(0, M, [np.sum(Ns), NSymbs])
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Modulate input data xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        # modulatedData has the data of all users (vertically stacked)
        modulatedData = self.modulator.modulate(inputData)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Perform the Interference Alignment xxxxxxxxxxxxxxxxxxxxxxxx
        cumNs = np.cumsum(Ns)
        # Split the data. transmit_signal will be a list and each element
        # is a numpy array with the data of a user
        transmit_signal = np.split(modulatedData, cumNs[:-1])

        self.multiUserChannel.randomize(Nr, Nt, K)
        #self.ia_solver.randomizeF(Ns)
        self.ia_solver.solve(Ns)

        transmit_signal_precoded = map(np.dot, self.ia_solver.F, transmit_signal)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Pass through the channel xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        noise_var = 1 / dB2Linear(SNR)
        self.ia_solver.noise_power = noise_var
        multi_user_channel = self.ia_solver._multiUserChannel
        # received_data is an array of matrices, one matrix for each receiver.
        received_data = multi_user_channel.corrupt_data(
            transmit_signal_precoded, noise_var)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Perform the Interference Cancelation xxxxxxxxxxxxxxxxxxxxxx
        received_data_no_interference = map(np.dot,
                                            self.ia_solver.W, received_data)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Demodulate Data xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        received_data_no_interference = np.vstack(received_data_no_interference)
        demodulated_data = self.modulator.demodulate(received_data_no_interference)
        # demodulated_data = map(self.modulator.demodulate, received_data_no_interference)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Debug xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        # print "IA Cost: {0:f}".format(self.ia_solver.get_cost())
        # print inputData - demodulated_data
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Calculates the symbol and bit error rates xxxxxxxxxxxxxxxxx
        symbolErrors = np.sum(inputData != demodulated_data)
        bitErrors = misc.count_bit_errors(inputData, demodulated_data)
        numSymbols = inputData.size
        numBits = inputData.size * modulators.level2bits(M)
        #ia_cost = self.ia_solver.get_cost()
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        # xxxxx Return the simulation results xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        symbolErrorsResult = Result.create(
            "symbol_errors", Result.SUMTYPE, symbolErrors)

        numSymbolsResult = Result.create(
            "num_symbols", Result.SUMTYPE, numSymbols)

        bitErrorsResult = Result.create("bit_errors", Result.SUMTYPE, bitErrors)

        numBitsResult = Result.create("num_bits", Result.SUMTYPE, numBits)

        berResult = Result.create("ber", Result.RATIOTYPE, bitErrors, numBits)

        serResult = Result.create(
            "ser", Result.RATIOTYPE, symbolErrors, numSymbols)

        # ia_costResult = Result.create(
        #     "ia_cost", Result.RATIOTYPE, ia_cost, 1)

        simResults = SimulationResults()
        simResults.add_result(symbolErrorsResult)
        simResults.add_result(numSymbolsResult)
        simResults.add_result(bitErrorsResult)
        simResults.add_result(numBitsResult)
        simResults.add_result(berResult)
        simResults.add_result(serResult)
        #simResults.add_result(ia_costResult)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        return simResults

    # def _keep_going(self, current_parameters, simulation_results):
    #     #return True
    #     cumulated_bit_errors = simulation_results['bit_errors'][-1].get_result()
    #     return cumulated_bit_errors < self.max_bit_errors

    # def get_data_to_be_plotted(self):
    #     """The get_data_to_be_plotted is not part of the simulation, but it
    #     is useful after the simulation is finished to get the results
    #     easily for plot.
    #     """
    #     ber = self.results.get_result_values_list('ber')
    #     ser = self.results.get_result_values_list('ber')

    #     # Get the SNR from the simulation parameters
    #     SNR = np.array(self.params['SNR'])

    #     return (SNR, ber, ser)


# xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# xxxxxxxxxx Main xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# Run the MaxSINRSimulationRunner and plot the results
if __name__ == '__main__':
    from pylab import *
    from util import simulations, misc

    from apps.simulate_ia_maxsinr import MaxSINRSimulationRunner

    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    # File name (without extension) for the figure and result files.
    results_filename = 'ia_max_sinr_results_4PSK_4x4(2)'
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # xxxxxxxxxx Performs the actual simulation xxxxxxxxxxxxxxxxxxxxxxxxxxx
    runner = MaxSINRSimulationRunner('ia_config_file.txt')
    pprint(runner.params.parameters)
    print(runner.ia_solver.__class__)
    runner.simulate()
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # xxxxxxxxxx Save the simulation results to a file xxxxxxxxxxxxxxxxxxxx
    runner.results.save_to_file('{0}.pickle'.format(results_filename))
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    #
    #
    print "Elapsed Time: {0}".format(runner.elapsed_time)
    #
    #
    #xxxxxxxxxx Load the results from the file xxxxxxxxxxxxxxxxxxxxxxxxxxxx
    #results_filename = 'ia_max_sinr_results'
    results = simulations.SimulationResults.load_from_file(
        '{0}{1}'.format(results_filename, '.pickle'))

    #SNR, ber, ser = runner.get_data_to_be_plotted()
    ber = results.get_result_values_list('ber')
    ser = results.get_result_values_list('ser')

    # Get the SNR from the simulation parameters
    SNR = np.array(results.params['SNR'])

    K = results.params["K"]
    Nr = results.params["Nr"]
    Nt = results.params["Nt"]
    Ns = results.params["Ns"]
    modulator_name = runner.modulator.name

    # Can only plot if we simulated for more then one value of SNR
    if SNR.size > 1:
        semilogy(SNR, ber, '--g*', label='BER')
        semilogy(SNR, ser, '--b*', label='SER')
        xlabel('SNR')
        ylabel('Error')
        title('Max SINR IA Algorithm\nK={0}, Nr={1}, Nt={2}, Ns={3}, {4}'.format(K, Ns, Nt, Ns, modulator_name))
        legend()

        grid(True, which='both', axis='both')
        show()

    print "Runned iterations: {0}".format(results.runned_reps)
    print misc.pretty_time(results.elapsed_time)


# xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# xxxxxxxxxx Main xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# Run the MaxSINRSimulationRunner and plot the results
if __name__ == '__main__1':
    # Since we are using the parallel capabilities provided by IPython, we
    # need to create a client and then a view of the IPython engines that
    # will be used.
    from IPython.parallel import Client
    cl = Client()
    dview = cl.direct_view()

    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    # NOTE: Before running the code above, initialize the ipython
    # engines. One easy way to do that is to call the "ipcluster start"
    # command in a terminal.
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # Add the folder containing PyPhysim to the python path in all the
    # engines
    dview.execute('import sys')
    dview.execute('sys.path.append("{0}")'.format(parent_dir))

    from pylab import *
    from apps.simulate_ia_maxsinr import MaxSINRSimulationRunner

    runner = MaxSINRSimulationRunner()
    runner.simulate_in_parallel(dview)

    SNR, ber, ser = runner.get_data_to_be_plotted()

    # Can only plot if we simulated for more then one value of SNR
    if SNR.size > 1:
        semilogy(SNR, ber, '--g*', label='BER')
        semilogy(SNR, ser, '--b*', label='SER')
        xlabel('SNR')
        ylabel('Error')
        title('Max SINR IA Algorithm\nK={0}, Nr={1}, Nt={2}, Ns={3} System'.format(runner.K, runner.Nr, runner.Nt, runner.Ns))
        legend()

        grid(True, which='both', axis='both')
        show()

    print "Runned iterations: {0}".format(runner.runned_reps)
    print runner.elapsed_time