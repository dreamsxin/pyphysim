#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Module with implementation of Coordinated Multipoint (CoMP) algorithms.

In gerenal, the CoMP algorithm is applied to an MIMO Interference Channel
(MIMO-IC) scenario, where we have pairs of transmitters and receivers, each
transmitter sending information only to its intending receiver, but
interfering with the other receivers. Alternativelly, an external
interference source may also be presented, which will interfere with all
receivers. In order to model the MIMO-IC one can use the
:class:`.channels.MultiUserChannelMatrix` and
:class:`.channels.MultiUserChannelMatrixExtInt` classes.

The CoMP algorithm may or may not take the external interference source
into consideration. The CoMP algorithm is implemented here as the
:class:`CompExtInt` class and the different external interference handling
metrics are described in the following section.


External Interference Hangling Metrics
--------------------------------------

The way the external interference is treated in the CompExtInt class
basically consists of sacrificing streams to avoid dimensions strongly
occupied by external interference. In other words, instead of using all
available spatial dimensions only a subset (containing less or no external
interference) of these dimensions is used. One has to decised how many (if
any) dimensions will be sacrificed and for that difference metrics can be
used.

The different metrics implemented in the CompExtInt class are:

- None: No stream reduction and this external interference handling is
  performed.
- capacity: The Shannon capacity is used.
- effective_throughput: The expected throughput is used. The effective
  throughput consists of the nominal data rate (considering a modulator and
  number of used streams) times 1 minus the package error rate. Sacrificing
  streams will reduce the nominal data rate but the gained interference
  reduction also means better SIRN values and thus a lower package error
  rate.

The usage of the CompExtInt class is described in the following section.


CompExtInt usage
----------------

1. First create a CompExtInt object.
2. Set the desired external interference handling metric by calling the :meth:`.set_ext_int_handling_metric` method.
3. Call the :meth:`.perform_comp_no_waterfilling` method.

"""

__revision__ = "$Revision$"

import numpy as np

from subspace.projections import calcProjectionMatrix
from comm.blockdiagonalization import BlockDiaginalizer

# Used for debug
from comm import blockdiagonalization
from comm import channels
from util.misc import least_right_singular_vectors
from util.conversion import single_matrix_to_matrix_of_matrices, linear2dB

__all__ = ['CompExtInt']


def _calc_stream_reduction_matrix(Re_k, kept_streams):
    """Calculates the `P` matrix that performs the stream reduction such that
    the subspace of the remaining streams span the dimensions with the
    lowest interference (according to the external interference plus noise
    covariance matrix Re_k).

    Parameters
    ----------
    Re_k : 2D numpy array
        The external interference plus noise covariance matrix at a SINGLE
        receiver.
    kept_streams : int
        Number of streams that will be kept. This will be equal to the
        number of columns of the returned matrix.

    Returns
    -------
    Pk : 2D numpy array
        A matrix whose columns corresponding to the `kept_streams` least
        significant right singular vectors of Re_k.

    """
    min_Vs = least_right_singular_vectors(Re_k, kept_streams)[0]
    return min_Vs


class CompExtInt(BlockDiaginalizer):
    """Performs the Coordinated Multipoint transmission also taking into
    account the external interference.

    The CompExtInt class performs the block diagonalization characteristic
    to the joint transmission scenario where multiple base stations act as
    a single transmitter to send data to the users. However, in addition to
    what the BlockDiaginalizer class does the CompExtInt class can also
    take external interference into account.

    One way to reduce of eliminate the external interference is to
    sacrifice streams in directions strongly occupied by the external
    interference.

    Notes
    -----
    See the :class:`BlockDiaginalizer` class for details about the block
    diagonalization process.

    """

    def __init__(self, num_users, iPu, noise_var, pe):
        """Initializes the CompExtInt object.

        Parameters
        ----------
        num_users : int
            Number of users.
        iPu : float
            Power available for EACH user (in linear scale).
        noise_var : float
            Noise variance (power in linear scale).
        pe : float
            Power of the external interference source (in linear scale)
        """
        BlockDiaginalizer.__init__(self, num_users, iPu, noise_var)
        self.pe = pe

        # Function used to decide how many streams will be sacrificed to
        # mitigate external interference. This is set in the
        # set_ext_int_handling_metric method (as well as the _modulator and
        # _packet_length attributes)
        self._metric_func = None  # The default metric will be None
        self._modulator = None
        self._packet_length = None

    def set_ext_int_handling_metric(self, metric,
                                    modulator=None, packet_length=None):
        """Set the metric used to decide how many streams to sacrifice for
        external interference handling.

        - If `metric` is None, then no streams will be sacrificed and the
          external interference won't be mitigated.

        - If `metric` is 'capacity', then the the sum capacity will be used
          to decide how many streams to sacrifice. That is, the
          _calc_shannon_sum_capacity method will be used in
          perform_comp_no_waterfilling to map the calculated sinrs into a
          sum capacity and the number of sacrificed streams will be the one
          with the highest sum capacity.

        - If `metric` is 'effective_throughput' then the effective
          throughput will be used to decide how many streams to
          sacrifice. That is, the _calc_effective_throughput method will be
          used in perform_comp_no_waterfilling to map the calculated sinrs into an
          effective throughput and the number of sacrificed streams will be
          the one with the highest sum capacity.

        Notes
        -----
        If `metric` is 'effective_throughput' then the modulator and
        packet_length arguments must be provided. For the other metric
        options they will be ignored if provided.

        Parameters
        ----------
        metric : str, {None, 'capacity', 'effective_throughput'}
            The metric name. Must be one of the available metrics.
        modulator : comm.modulators.Modulator object
            The modulator object used in the simulation. It will be used to
            calculate the theoretical BER and (with the packet length,
            the theoretical PER)
        packet_length : int
            The packet length used in the simulation.

        Raises
        ------
        AttributeError
            If the metric is not one of {None, 'capacity',
            'effective_throughput'}

        """
        if metric is None or metric == 'None':
            self._metric_func = None
            self._modulator = None
            self._packet_length = None

        elif metric == 'capacity':
            self._metric_func = self._calc_shannon_sum_capacity
            self._modulator = None
            self._packet_length = None

        elif metric == 'effective_throughput':
            self._metric_func = self._calc_effective_throughput
            if (modulator is None) or (packet_length is None):
                raise AttributeError("The modulator and packet_length attributes must be provided for the 'effective_throughput' metric.")
            self._modulator = modulator
            self._packet_length = packet_length
        else:
            raise AttributeError("The `metric` attribute can only be one of {None, 'capacity', 'effective_throughput'}")

    @staticmethod
    def calc_receive_filter_user_k(Heq_k_P, P=None):
        """Calculates the Zero-Forcing receive filter of a single user `k`
        with or without the stream reduction.

        Parameters
        ----------
        Heq_k_P : 2D numpy array
            The equivalent channel of user `k` after the block
            diagonalization process and any stream reduction.
        P : 2D numpy array
            P has the most significant singular vectors of the external
            interference plus noise covariance matrix for each
            receiver.

        Returns
        -------
        W : 2D numpy array
            The receive filter of user `k`.

        Notes
        -----
        If `P` is not None then the number of transmit streams will be
        equal to the number of columns in `P`. Also, the receive filter `W`
        includes a projection into the subspace spanned by the columns of
        `P`. Since `P` was calculated to be in the directions with weaker
        (or no) external interference then the receive filter `W` will
        mitigate the external interference.

        """
        if P is None:
            W = np.linalg.pinv(Heq_k_P)
        else:
            overbar_P = calcProjectionMatrix(P)

            # Calculate the equivalent channel including the stream
            # reduction
            #Heq_k_red = np.dot(Heq_k, P)
            W = np.dot(
                np.linalg.pinv(np.dot(overbar_P, Heq_k_P)),
                overbar_P)

        return W

    # NOTE: PROBABLY, THIS IS ONLY VALID FOR SPATIAL MULTIPLEXING.
    @staticmethod
    def _calc_linear_SINRs(Heq_k_red, Wk, Re_k):
        """Calculates the effective SINRs of each parallel channel.

        Parameters
        ----------
        Heq_k_red : 2D numpy array
            Equivalent channel matrix of user `k` including the block
            diagonalization and any stream reduction applied.
        Wk : 2D numpy array
            Receive filter for user `k`.
        Re_k : 1D numpy array of 2D numpy arrays.
            A numpy array where each element is the covariance matrix of
            the external interference PLUS noise seen by a user.

        Returns
        -------
        sinrs : 1D numpy array
            SINR (in linear scale) of all the parallel channels of all users.

        """
        #K = Re_k.size  # Number of users
        mtP = np.dot(Wk, Heq_k_red)
        desired_power = np.abs(np.diagonal(mtP)) ** 2
        internalInterference = np.sum(np.abs((mtP - np.diagflat(np.diagonal(mtP)))) ** 2, 1)

        Wk_H = Wk.transpose().conjugate()

        # Note that the noise is already accounted in the covariance matrix
        # Re_k
        external_interference_plus_noise = np.diagonal(
            np.dot(Wk, np.dot(Re_k, Wk_H))
        ).real

        sinr = desired_power / (internalInterference + np.abs(external_interference_plus_noise))
        return sinr

    # Since only `sinrs` is required and it is passed as an argument, then
    # this method is a static method.
    @staticmethod
    def _calc_shannon_sum_capacity(sinrs):
        """Calculate the sum of the Shannon capacity of the values in `sinrs`

        Parameters
        ----------
        sinrs : 1D numpy array or float
            SINR values (in linear scale).

        Returns
        -------
        sum_capacity : float
            Sum capacity.

        Examples
        --------
        >>> sinrs_linear = np.array([11.4, 20.3])
        >>> print CompExtInt._calc_shannon_sum_capacity(sinrs_linear)
        8.04504974084
        """
        sum_capacity = np.sum(np.log2(1 + sinrs))

        return sum_capacity

    def _calc_effective_throughput(self, sinrs):
        """Calculates the effective throughput of the values in `sinrs`.

        The effective throughput is equivalent to the packet error for a
        specific packet error rate and packet length, times the nominal
        throughput.

        Parameters
        ----------
        sinrs : 1D numpy array or float
            SINR values (in linear scale).

        Returns
        -------
        effective_throughput : float
            Effective throughput that can be obtained.

        """
        SINRs = linear2dB(sinrs)
        se = self._modulator.calcTheoreticalSpectralEfficiency(SINRs, self._packet_length)
        total_se = np.sum(se)

        return total_se

    def perform_comp_no_waterfilling(self, mu_channel):
        """Perform the block diagonalization of `mu_channel` taking the
        external interference into account.

        This is the main method calculating the CoMP algorithm. Two
        important parameters used here are the noise variance (an attribute
        of the `mu_channel` object) and the external interference power
        (the `pe` attribute) attributes.

        Parameters
        ----------
        mu_channel : MultiUserChannelMatrixExtInt object.
            A MultiUserChannelMatrixExtInt object, which has the channel
            from all the transmitters to all the receivers, as well as th
            external interference.

        Returns
        -------
        MsPk_all_users : 1D numpy array of 2D numpy arrays
            A 1D numpy array where each element corresponds to the precoder
            for a user.
        Wk_all_users : 1D numpy array of 2D numpy arrays
            A 1D numpy array where each element corresponds to the receive
            filter for a user.
        Ns_all_users: 1D numpy array of ints
            Number of streams of each user.

        """
        K = mu_channel.K
        Nr = mu_channel.Nr
        Nt = mu_channel.Nt
        H_matrix = mu_channel.big_H_no_ext_int
        Re = mu_channel.calc_cov_matrix_extint_plus_noise(
            self.noise_var, self.pe)

        # xxxxx Output variables xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        MsPk_all_users = np.empty(K, dtype=np.ndarray)
        Wk_all_users = np.empty(K, dtype=np.ndarray)
        Ns_all_users = np.empty(K, dtype=int)
        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

        if self._metric_func is None:
            # If _metric_func is None then we are not handling external
            # interference. Therefore, we simple call the
            # block_diagonalize_no_waterfilling method from the
            # BlockDiaginalizer class.
            (newH, Ms_good) = BlockDiaginalizer.block_diagonalize_no_waterfilling(self, mu_channel.big_H_no_ext_int)

            # Since there is no stream reduction, the number of streams of
            # each user is equal to the number of transmit antennas of that
            # user
            Ns_all_users = Nt
            MsPk_all_users = single_matrix_to_matrix_of_matrices(Ms_good, None, Nt)
            newH_all_k = single_matrix_to_matrix_of_matrices(newH, Nr, Nt)
            for userindex in range(K):
                Wk_all_users[userindex] = self.calc_receive_filter_user_k(
                    newH_all_k[userindex, userindex], None)

            return (MsPk_all_users, Wk_all_users, Ns_all_users)

        # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        # If we are here, then _metric_func is not None, which means that
        # we are handling the external interference.
        Ms_bad, Sigma = self._calc_BD_matrix_no_power_scaling(H_matrix)

        # The k-th 'element' in Ms_bad_ks is a matrix containing the
        # columns of Ms_bad of the k-th user.
        Ms_bad_ks = single_matrix_to_matrix_of_matrices(Ms_bad, None, Nt)
        H_all_ks = single_matrix_to_matrix_of_matrices(H_matrix, Nr)

        # Loop for the users
        for userindex in range(K):
            Ntk = Nt[userindex]
            Rek = Re[userindex]
            Hk = H_all_ks[userindex]
            Msk = Ms_bad_ks[userindex]

            # Equivalent channel of user k after the block diagonalization
            # process, but without any stream reduction
            Heq_k = np.dot(Hk, Msk)

            # We can have from a single stream to all streams (the number
            # of transmit antennas). This loop varies the number of
            # transmit streams of user k.
            #
            # The metric that will be calculated and used to determine how
            # many streams to sacrifice.
            metric_value_for_user_k = np.zeros(Ntk)
            Pk_all = np.empty(Ntk, dtype=np.ndarray)
            norm_term_all = np.empty(Ntk)
            Wk_all = np.empty(Ntk, dtype=np.ndarray)
            for index in range(Ntk):
                Ns_k = index + 1
                # Find Pk
                Pk = _calc_stream_reduction_matrix(Rek, Ns_k)
                Pk_all[index] = Pk  # Save for later

                # Normalization term for the combined BD matrix Msk and stream
                # reduction matrix Pk
                norm_term = np.linalg.norm(np.dot(Msk, Pk), 'fro') / np.sqrt(self.iPu)
                norm_term_all[index] = norm_term  # Save for later

                # Equivalent channel with stream reduction
                Heq_k_red = np.dot(Heq_k, Pk / norm_term)

                # Calculates the receive filter W_k (note
                # calc_receive_filter_user_k receives the channel without
                # stream reduction as an argument and the stream reduction
                # matrix)
                W_k = self.calc_receive_filter_user_k(Heq_k_red, Pk)
                Wk_all[index] = W_k

                # SINR (in linear scale) of all streams of user k.
                sinrs_k = self._calc_linear_SINRs(Heq_k_red, W_k, Rek)

                #metric_value_for_user_k[index] = self._calc_shannon_sum_capacity(sinrs_k)
                metric_value_for_user_k[index] = self._metric_func(sinrs_k)

            # The index with the highest metric value. This is equivalent
            # to the number of transmit streams which yields the highest
            # value of the metric.
            best_index = np.argmax(metric_value_for_user_k)
            MsPk_all_users[userindex] = np.dot(Msk, Pk_all[best_index]) / norm_term_all[best_index]
            Wk_all_users[userindex] = Wk_all[best_index]
            Ns_all_users[userindex] = Pk_all[best_index].shape[1]

        return (MsPk_all_users, Wk_all_users, Ns_all_users)


# # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# def perform_comp_no_waterfilling(mtChannel, num_users, iPu, noise_var):
#     """Performs the block diagonalization of `mtChannel`.

#     Parameters
#     ----------
#     mtChannel : 2D numpy array
#             Channel from the transmitter to all users.
#     num_users : int
#         Number of users
#     iPu : float
#         Power available for each user
#     noise_var : float
#         Noise variance

#     Returns
#     -------
#     (newH, Ms_good) : A tuple of numpy arrays
#         newH is a 2D numpy array corresponding to the Block
#         diagonalized channel, while Ms_good is a 2D numpy array
#         corresponding to the precoder matrix used to block diagonalize
#         the channel.

#     """
#     COMP = Comp(num_users, iPu, noise_var)
#     results_tuble = COMP.perform_comp_no_waterfilling(mtChannel)
#     return results_tuble


# def perform_comp_with_ext_int_no_waterfilling(mu_channel, num_users, iPu, noise_var, pe):
#     """Perform the block diagonalization of `mtChannel` taking the external
#     interference into account.

#     Parameters
#     ----------
#     mu_channel : MultiUserChannelMatrixExtInt object.
#         A MultiUserChannelMatrixExtInt object, which has the channel from
#         all the transmitters to all the receivers, as well as th external
#         interference.
#     num_users : int
#         Number of users
#     iPu : float
#         Power available for each user
#     noise_var : float
#         Noise variance
#     pe : float
#         Interference source power.

#     Returns
#     -------
#     output : lalala
#         Write me

#     """
#     COMP = CompExtInt(num_users, iPu, noise_var, pe)
#     results_tuble = COMP.perform_comp_no_waterfilling(mu_channel)
#     return results_tuble


if __name__ == '__main__1':  # pragma: no cover
    Pu = 5.
    noise_var = 0.1
    num_users = 3
    num_antenas = 2
    # Total number of transmit and receive antennas
    #iNr = iNt = num_antenas * num_users

    #channel = randn_c(iNr, iNt)

    channel = channels.MultiUserChannelMatrix()
    channel.randomize(num_antenas, num_antenas, num_users)

    (newH, Ms) = blockdiagonalization.block_diagonalize(
        channel.big_H, num_users, Pu, noise_var)

    print newH.shape

    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    # xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    for userindex in range(num_users):
        # Code here will be moved to the CompExtInt.perform_comp_no_waterfilling method later
        mtW_bd = np.linalg.pinv(newH)
        mtP = np.dot(mtW_bd, newH)

        # SNR without interference handling
        desired_power = np.abs(np.diag(mtP)) ** 2
        internalInterference = np.sum(np.abs(mtP - np.diag(np.diag(mtP))) ** 2, axis=1)

        #externalInterference =

        print internalInterference

        a = np.array([[1, 2, 3], [4, 5, 6], [5, 4, 3]])
        print a
        print np.sum(a, axis=1)
