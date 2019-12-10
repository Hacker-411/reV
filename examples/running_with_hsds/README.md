# Using HSDS
The Highly Scalable Distributed Service (HSDS) is a cloud optimized API to enable access to .h5 files hosted on AWS. The HSDS software was developed by the [HDF Group](https://www.hdfgroup.org/) and is hosted on Amazon Web Services (AWS) using a combination of EC2 (Elastic Compute) and S3 (Scalable Storage Service). You can read more about the HSDS service [in this slide deck](https://www.slideshare.net/HDFEOS/hdf-cloud-services).

## Setting up HSDS

To get started install the h5pyd library:

```
pip install h5pyd
conda install h5pyd
```

Next, configure h5pyd by running `hsconfigure` from the command line, or by creating a configuration file at ~/.hscfg:

```
hsconfigure
hs_endpoint = https://developer.nrel.gov/api/hsds
hs_username = None
hs_password = None
hs_api_key = 3K3JQbjZmWctY0xmIfSYvYgtIcM3CN0cb1Y2w9bf
```

*The example API key here is for demonstation and is rate-limited per IP. To get your own API key, visit https://developer.nrel.gov/signup/*

*Please note that our HSDS service is for demonstration purposes only, if you would like to use HSDS for production runs of reV please setup your own service: https://github.com/HDFGroup/hsds and point it to our public HSDS bucket: s3://nrel-pds-hsds*

## Using HSDS with reV
Once h5pyd has been installed and configured, reV can pull data directly from AWS using HSDS. To access the resource data used by reV (NSRDB or WTK) you have to turn on the `hsds` flag in the [resource handlers](https://github.com/NREL/reV/blob/master/reV/handlers/resource.py):

```
nsrdb_dir = '/nrel/nsrdb/'
nsrdb_file = os.path.join(nsrdb_dir, 'nsrdb_2013.h5')
with reV.Resource(nsrdb_file, hsds=True) as f:
    meta_data = f.meta
    time_index = f.time_index
```

reV generation (reV.Gen) will automatically infer if a file path is locally on disk or from HSDS:

```
gen = reV.Gen.reV_run(tech='pv', points=points, sam_files=config_path,
                      res_file=nsrdb_file, max_workers=1, fout=None,
                      output_request=('cf_mean', 'cf_profile'))
```
For a fully operable HSDS example please see: https://github.com/NREL/reV/blob/master/examples/running_with_hsds/reV_HSDS_Examples.ipynb