#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division

""" Convert 2dfdr reduced images to extracted spectra in GALAH FITS format. """

__author__ = "Andy Casey <arc@ast.cam.ac.uk>"

__all__ = ["from_2dfdr"]

from subprocess import check_output

import numpy as np

from astropy.io import fits
from astropy.constants import c as speed_of_light
import motions

# [TODO] Read the GAP version from elsewhere.
WG6_VER = 6.4
WG6_GIT_HASH = check_output("git rev-parse --short HEAD".split()).strip()

def get_ccd_number(image):
    ccd_name = image[0].header.comments["SPLYTEMP"].split(" ")[0]
    ccd_number = {
        "BLUE": 1,
        "GREEN": 2,
        "RED": 3,
        "IR": 4
    }[ccd_name]

    return (ccd_number, ccd_name)

def verify_hermes_origin(image):
    assert image[0].header["ORIGIN"].strip() == "AAO"
    assert image[0].header["INSTRUME"].strip() == "HERMES-2dF"


def read_2dfdr_extensions(image):
    """
    Parse the image extensions from a 2dfdr-reduced image.

    :param image:
        The 2dfdr-reduced image.

    :type image:
        :class:`astropy.io.fits.hdu.hdulist.HDUList`
    """

    extensions = {
        "data": 0,
    }
    extnames = map(str.strip, [hdu.header.get("EXTNAME", "") for hdu in image])
    extensions["variance"] = extnames.index("VARIANCE")
    extensions["fibres"] = extnames.index("FIBRES")
    return extensions


def dummy_normalisation_hdus(hdulist=None):
    """
    Return dummy HDUs for the normalised flux and associated uncertainty.
    """

    # Return a dummy normalisation HDU
    hdu_normed_flux = fits.ImageHDU(data=None, header=None,
        do_not_scale_image_data=True)

    # Add header information
    if hdulist is not None:
        for key in ("CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1"):
            hdu_normed_flux.header[key] = hdulist[0].header[key]
            hdu_normed_flux.header.comments[key] = hdulist[0].header.comments[key]

    hdu_normed_flux.header["EXTNAME"] = "normalised_spectrum"
    hdu_normed_flux.header.comments["EXTNAME"] = "Normalised spectrum flux"
    hdu_normed_flux.add_checksum()

    hdu_normed_sigma = fits.ImageHDU(data=None, header=None,
        do_not_scale_image_data=True)

    # Add header information
    if hdulist is not None:
        for key in ("CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1"):
            hdu_normed_sigma.header[key] = hdulist[0].header[key]
            hdu_normed_sigma.header.comments[key] = hdulist[0].header.comments[key]

    hdu_normed_sigma.header["EXTNAME"] = "normalised_sigma"
    hdu_normed_sigma.header.comments["EXTNAME"] = "Normalised flux sigma"
    hdu_normed_sigma.add_checksum()

    return [hdu_normed_flux, hdu_normed_sigma]


def dummy_ccf_hdu(hdulist=None):
    """
    Return a dummy HDU for the best-fitting cross-correlation function.
    """

    hdu_ccf = fits.ImageHDU(data=None, header=None,
        do_not_scale_image_data=True)

    hdu_ccf.header["EXTNAME"] = "CCF"
    hdu_ccf.header.comments["EXTNAME"] = "CCF from best-fitting template"

    # Add empty default values.
    default_headers = [
        ("VRAD", "Radial velocity (km/s)"),
        ("U_VRAD", "Uncertainty on radial velocity (km/s)"),
        ("TEFF", "Effective temperature"),
        ("LOGG", "Surface gravity"),
        ("FE_H", "Metallicity ([Fe/H])"),
        ("ALPHA_FE", "Alpha-enhancement ([alpha/Fe])")
    ]
    for key, comment in default_headers:
        hdu_ccf.header[key] = "NaN"
        hdu_ccf.header.comments[key] = comment
    
    hdu_ccf.add_checksum()

    return hdu_ccf
    


def from_2dfdr(reduced_filename, dummy_hdus=True):
    """
    Returns a list of `astropy.io.fits.hdu.hdulist.HDUList` objects
    (1 HDU List per 2dfdr program object).

    :param reduced_filename:
        The path of the 2dfdr-reduced combined filename.

    :type reduced_filename:
        str
    """

    image = fits.open(reduced_filename)
    ext = read_2dfdr_extensions(image)

    # Verifications
    verify_hermes_origin(image)

    # Header modifications.
    keywords = ["NAME", "RA", "DEC", "PMRA", "PMDEC", ("MAGNITUDE", "MAG"),
        ("COMMENT", "DESCR")]
    keyword_comments = {
        "NAME": "Input source name",
        "DESCR": "Input source comment",
        "MAG": "Input source magnitude",
        "RA": "Right Ascension (degrees)",
        "DEC": "Declination (degrees)",
        "PMRA": "Proper motion in RA (mas/yr)",
        "PMDEC": "Proper motion in DEC (mas/yr)",
        "FIBRE": "Fibre number"
    }
    header_template = image[ext["data"]].header.copy()
    for column in ("CDELT2", "CRPIX2", "CRVAL2", "CTYPE2", "CUNIT2"):
        del header_template[column]

    ccd_number, ccd_name = get_ccd_number(image)
    header_template["CCD"] = ccd_number
    header_template.comments["CCD"] = "{0} camera".format(ccd_name)
    header_template["WG6_VER"] = WG6_VER
    header_template["WG6_HASH"] = WG6_GIT_HASH
    header_template.comments["WG6_VER"] = "WG6 standardisation code version"
    header_template.comments["WG6_HASH"] = "WG6 standardisation commit hash"

    extracted_sources = []
    for program_index in np.where(image[ext["fibres"]].data["TYPE"] == "P")[0]:

        flux = image[ext["data"]].data[program_index, :]
        variance = image[ext["variance"]].data[program_index, :]
        header = header_template.copy()

        # Add header information from the fibre table.
        header["FIBRE"] = program_index + 1
        fibre_header = image[ext["fibres"]].data[program_index]
        for keyword in keywords:
            if isinstance(keyword, (str, unicode)):
                header[keyword] = fibre_header[keyword]
            else:
                from_keyword, to_keyword = keyword
                header[to_keyword] = fibre_header[from_keyword]

        # The 2dfdr fibre table has the RA and DEC in radians. Who does that?!
        if "RA" in keywords and "DEC" in keywords:
            header["RA"] *= 180./np.pi
            header["DEC"] *= 180./np.pi

        # Add associated comments for the fibre table information
        for keyword, comment in keyword_comments.items():
            header.comments[keyword] = comment

        # Create the HDUList.
        hdu_flux = fits.PrimaryHDU(data=flux, header=header,
            do_not_scale_image_data=True)
        hdu_flux.header["EXTNAME"] = "input_spectrum"
        hdu_flux.header.comments["EXTNAME"] = "Spectrum flux"

        hdu_sigma = fits.ImageHDU(data=variance**0.5, header=None,
            do_not_scale_image_data=True)
        
        for key in ("CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1"):
            hdu_sigma.header[key] = hdu_flux.header[key]
            hdu_sigma.header.comments[key] = hdu_flux.header.comments[key]
        hdu_sigma.header["EXTNAME"] = "input_sigma"
        hdu_sigma.header.comments["EXTNAME"] = "Flux sigma"

        # Calculate the barycentric motion.
        v_bary, v_helio = motions.from_header(header)

        hdu_flux.header["V_BARY"] = v_bary.to("km/s").value
        hdu_flux.header["V_HELIO"] = v_helio.to("km/s").value
        hdu_flux.header.comments["V_BARY"] = "Barycentric motion (km/s)"
        hdu_flux.header.comments["V_HELIO"] = "Heliocentric motion (km/s)"
        hdu_flux.header["HISTORY"] = "Corrected for barycentric motion (V_BARY)"

        # Add motion correction information.
        for hdu in (hdu_flux, hdu_sigma):
            hdu.header["CRVAL1"] *= 1. + (v_bary/speed_of_light).value
            hdu.header["CDELT1"] *= 1. + (v_bary/speed_of_light).value

        # Create HDUList and add checksums.
        hdulist = fits.HDUList([hdu_flux, hdu_sigma])
        [hdu.add_checksum() for hdu in hdulist]

        # Add dummy extensions
        if dummy_hdus:
            hdulist.extend(dummy_normalisation_hdus(hdulist))
            hdulist.append(dummy_ccf_hdu(hdulist))

        extracted_sources.append(hdulist)

    image.close()

    return extracted_sources



